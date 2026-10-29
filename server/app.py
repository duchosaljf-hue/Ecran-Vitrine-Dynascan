"""Application web : gestion des biens, des écrans et des comptes, et API des players."""
import hashlib
import json
import logging
import re
import secrets
import shutil
import sqlite3
import threading
import time
import unicodedata
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Literal, Optional, Union

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import auth, media, monitor
from .config import CFG, VERSION
from .db import (add_days, audit, db, get_settings, init_db, iso, jloads, local_tz, now_iso, parse_iso,
                 set_settings, today_local, utcnow)

log = logging.getLogger("vitrine")

BADGES = {"": "", "nouveau": "Nouveau", "baisse": "Baisse de prix", "exclusif": "Exclusivité",
          "compromis": "Sous compromis", "vendu": "Vendu"}
VIEW_TYPES = {"texte", "qr", "agence"}
ONLINE_SECONDS = 150

JOB_SEM = threading.Semaphore(2)
BUSY = {}          # item_id -> libellé du traitement en cours
BUSY_LOCK = threading.Lock()


# =============================================================== utilitaires
def valid_date(value, field):
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError:
        raise HTTPException(400, f"Date invalide pour « {field} » (format AAAA-MM-JJ).")


def valid_hhmm(value, field):
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(value or "")):
        raise HTTPException(400, f"Heure invalide pour « {field} » (format HH:MM).")
    return value


def screens_slugs(conn):
    return [r["slug"] for r in conn.execute("SELECT slug FROM screens ORDER BY id")]


def normalise_screens(value, conn):
    if value in (None, "", "*") or value == ["*"]:
        return "*"
    if isinstance(value, str):
        value = [value]
    known = set(screens_slugs(conn))
    cleaned = [s for s in value if s in known]
    if not cleaned:
        raise HTTPException(400, "Choisissez au moins un écran.")
    if set(cleaned) == known:
        return "*"
    return json.dumps(cleaned)


def screens_list(raw):
    if raw == "*" or not raw:
        return "*"
    return jloads(raw, "*")


def item_state(row, today, busy):
    if row["status"] == "trash":
        return "trash"
    if row["status"] == "draft":
        return "draft"
    if busy and not jloads(row["slides"], []) and row["kind"] != "view":
        return "processing"
    if row["start_date"] and row["start_date"] > today.isoformat():
        return "scheduled"
    if row["end_date"] and row["end_date"] < today.isoformat():
        return "expired"
    return "live"


def effective_badge(row, today):
    if not row["badge"]:
        return ""
    if row["badge_until"] and row["badge_until"] < today.isoformat():
        return ""
    return row["badge"]


def item_dict(row, today, settings=None, detail=False):
    with BUSY_LOCK:
        busy = row["id"] in BUSY
    slides = jloads(row["slides"], [])
    state = item_state(row, today, busy)
    days_left = None
    if row["end_date"]:
        try:
            days_left = (date.fromisoformat(row["end_date"]) - today).days
        except ValueError:
            pass
    data = {
        "id": row["id"], "kind": row["kind"], "title": row["title"], "ref": row["ref"], "notes": row["notes"],
        "status": row["status"], "state": state, "busy": busy, "error": row["error"],
        "duration": row["duration"], "weight": row["weight"],
        "start_date": row["start_date"], "end_date": row["end_date"], "days_left": days_left,
        "badge": row["badge"], "badge_until": row["badge_until"], "badge_active": effective_badge(row, today),
        "screens": screens_list(row["screens"]), "price": row["price"], "thumb": row["thumb"],
        "pages": len(slides), "view_type": row["view_type"],
        "created_at": row["created_at"], "updated_at": row["updated_at"],
    }
    if detail:
        data["slides"] = slides
        data["view_data"] = jloads(row["view_data"], {})
    return data


def get_item_or_404(conn, item_id):
    row = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Bien introuvable.")
    return row


def username_by_id(conn, user_id):
    row = conn.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()
    return row["username"] if row else ""


# =============================================================== modèles
class LoginBody(BaseModel):
    username: str
    password: str


class PasswordBody(BaseModel):
    current: str = ""
    new: str


class ItemPatch(BaseModel):
    title: Optional[str] = None
    ref: Optional[str] = None
    notes: Optional[str] = None
    status: Optional[Literal["active", "draft"]] = None
    duration: Optional[int] = None
    weight: Optional[int] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    badge: Optional[str] = None
    badge_until: Optional[str] = None
    screens: Optional[Union[str, List[str]]] = None
    price: Optional[int] = None
    view_data: Optional[dict] = None


class ViewCreate(BaseModel):
    view_type: Literal["texte", "qr", "agence"]
    title: str = ""
    view_data: dict = Field(default_factory=dict)


class UserCreate(BaseModel):
    username: str
    display_name: str = ""
    role: Literal["admin", "user"] = "user"
    password: str = ""


class UserPatch(BaseModel):
    display_name: Optional[str] = None
    role: Optional[Literal["admin", "user"]] = None
    active: Optional[bool] = None


class ScreenCreate(BaseModel):
    name: str
    slug: str = ""
    orientation: Literal["portrait", "landscape"] = "portrait"


class ScreenPatch(BaseModel):
    name: Optional[str] = None
    orientation: Optional[Literal["portrait", "landscape"]] = None
    order_mode: Optional[Literal["random", "recent"]] = None
    default_duration: Optional[int] = None
    weather_every: Optional[int] = None
    weather_seconds: Optional[int] = None
    corner_clock: Optional[bool] = None
    fit: Optional[Literal["contain", "blur", "cover"]] = None
    ticker: Optional[str] = None
    open_time: Optional[str] = None
    close_time: Optional[str] = None
    hours_enabled: Optional[bool] = None


class OverrideBody(BaseModel):
    text: str
    minutes: int = 60


class PauseBody(BaseModel):
    minutes: int = 30


class SettingsBody(BaseModel):
    agency_name: Optional[str] = None
    agency_address: Optional[str] = None
    agency_phone: Optional[str] = None
    agency_hours: Optional[str] = None
    agency_site: Optional[str] = None
    city: Optional[str] = None
    latitude: Optional[str] = None
    longitude: Optional[str] = None
    timezone: Optional[str] = None
    default_duration: Optional[int] = None
    default_weight: Optional[int] = None
    new_badge_days: Optional[int] = None
    default_screen: Optional[str] = None
    alerts_enabled: Optional[bool] = None
    alert_to: Optional[str] = None
    smtp_host: Optional[str] = None
    smtp_port: Optional[int] = None
    smtp_security: Optional[Literal["starttls", "ssl", "none"]] = None
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_from: Optional[str] = None


# =============================================================== traitement des fichiers
def save_upload(upload: UploadFile) -> Path:
    suffix = Path(upload.filename or "").suffix.lower()
    if suffix not in media.ALLOWED_EXT:
        raise HTTPException(400, "Type de fichier non accepté (PDF, JPG, PNG, WebP, GIF, MP4, MOV, WebM).")
    CFG.ensure_dirs()
    tmp = CFG.tmp_dir / f"{secrets.token_hex(8)}{suffix}"
    limit = CFG.max_upload_mb * 1024 * 1024
    size = 0
    with open(tmp, "wb") as out:
        while True:
            chunk = upload.file.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > limit:
                out.close()
                tmp.unlink(missing_ok=True)
                raise HTTPException(413, f"Fichier trop volumineux (maximum {CFG.max_upload_mb} Mo).")
            out.write(chunk)
    if size == 0:
        tmp.unlink(missing_ok=True)
        raise HTTPException(400, "Fichier vide.")
    return tmp


def start_job(item_id, tmp_path, new_rev, username, replacing, filename):
    with BUSY_LOCK:
        BUSY[item_id] = "traitement"
    threading.Thread(target=run_job, args=(item_id, tmp_path, new_rev, username, replacing, filename),
                     daemon=True, name=f"media-{item_id}").start()


def run_job(item_id, tmp_path, new_rev, username, replacing, filename):
    try:
        with JOB_SEM:
            kind, slides, thumb, info = media.process_file(tmp_path, item_id, new_rev)
        with db() as conn:
            row = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
            if not row:
                media.delete_item_files(item_id)
                return
            settings = get_settings(conn)
            today = today_local(settings)
            old_rev, old_price = row["rev"], row["price"]
            title = row["title"]
            if not title or (not replacing and title == Path(filename).stem):
                title = info.get("title") or title or Path(filename).stem
            new_price = info.get("price", old_price)
            badge, badge_until, detail = row["badge"], row["badge_until"], f"fichier « {filename} »"
            if replacing and old_price and new_price and new_price < old_price:
                badge = "baisse"
                badge_until = add_days(today, int(settings["new_badge_days"]))
                detail += f" · prix {old_price:,} € → {new_price:,} €".replace(",", " ")
            elif new_price:
                detail += f" · prix {new_price:,} €".replace(",", " ")
            conn.execute(
                "UPDATE items SET kind = ?, slides = ?, thumb = ?, rev = ?, title = ?, price = ?, badge = ?,"
                " badge_until = ?, error = NULL, updated_at = ? WHERE id = ?",
                (kind, json.dumps(slides), thumb, new_rev, title, new_price, badge, badge_until, now_iso(), item_id))
            audit(conn, username, "fichier-remplace" if replacing else "fichier-ajoute", item_id, detail)
        media.cleanup_old_revisions(item_id, {new_rev, old_rev})
    except Exception as exc:  # noqa: BLE001
        log.warning("traitement du fichier de l'élément %s impossible : %s", item_id, exc)
        with db() as conn:
            conn.execute("UPDATE items SET error = ?, updated_at = ? WHERE id = ?",
                         (str(exc)[:300], now_iso(), item_id))
            audit(conn, username, "erreur-fichier", item_id, str(exc)[:300])
    finally:
        Path(tmp_path).unlink(missing_ok=True)
        with BUSY_LOCK:
            BUSY.pop(item_id, None)


# =============================================================== application
@asynccontextmanager
async def lifespan(app):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    init_db()
    from .importer import import_inbox_once
    try:
        import_inbox_once()
    except Exception:  # noqa: BLE001
        log.exception("import de l'ancien dossier impossible")
    monitor.start()
    log.info("vitrine %s démarrée", VERSION)
    yield


app = FastAPI(title="Vitrine", version=VERSION, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def cache_headers(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/files/"):
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    elif "cache-control" not in response.headers:
        response.headers["Cache-Control"] = "no-cache"
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    if path.startswith("/api/") and not path.startswith("/api/player/"):
        response.headers["Cache-Control"] = "no-store"
    return response


# =============================================================== connexion
@app.post("/api/login")
def api_login(body: LoginBody, request: Request, response: Response):
    ip = request.client.host if request.client else ""
    with db() as conn:
        try:
            row = auth.login(conn, body.username, body.password, ip)
        except HTTPException as exc:
            conn.commit()  # conserve le compteur d'échecs
            raise exc
        token, csrf = auth.create_session(conn, row["id"])
        audit(conn, row["username"], "connexion", None, ip)
    response.set_cookie(auth.COOKIE, token, httponly=True, samesite="lax", max_age=CFG.session_hours * 3600, path="/")
    principal = auth.Principal(row, csrf)
    return {"user": principal.public(), "csrf": csrf}


@app.post("/api/logout")
def api_logout(request: Request, response: Response):
    token = request.cookies.get(auth.COOKIE)
    if token:
        with db() as conn:
            auth.destroy_session(conn, token)
    response.delete_cookie(auth.COOKIE, path="/")
    return {"ok": True}


@app.get("/api/me")
def api_me(principal=Depends(auth.current_principal)):
    return {"user": principal.public(), "csrf": principal.csrf}


@app.post("/api/me/password")
def api_change_password(body: PasswordBody, request: Request, principal=Depends(auth.require_user)):
    auth.check_password_strength(body.new)
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (principal.id,)).fetchone()
        if not principal.must_change_pw and not auth.verify_password(body.current, row["pw_hash"]):
            raise HTTPException(400, "Mot de passe actuel incorrect.")
        if auth.verify_password(body.new, row["pw_hash"]):
            raise HTTPException(400, "Choisissez un mot de passe différent de l'actuel.")
        conn.execute("UPDATE users SET pw_hash = ?, must_change_pw = 0 WHERE id = ?",
                     (auth.hash_password(body.new), principal.id))
        token = request.cookies.get(auth.COOKIE, "")
        conn.execute("DELETE FROM sessions WHERE user_id = ? AND token_hash != ?",
                     (principal.id, auth._token_hash(token)))
        audit(conn, principal.username, "mot-de-passe-change")
    return {"ok": True}


# =============================================================== utilisateurs (admin)
def user_dict(row):
    return {"id": row["id"], "username": row["username"], "display_name": row["display_name"],
            "role": row["role"], "active": bool(row["active"]), "must_change_pw": bool(row["must_change_pw"]),
            "last_login": row["last_login"], "created_at": row["created_at"]}


@app.get("/api/users")
def api_users(principal=Depends(auth.require_ready_admin)):
    with db() as conn:
        rows = conn.execute("SELECT * FROM users ORDER BY role, username").fetchall()
    return {"users": [user_dict(r) for r in rows]}


@app.post("/api/users")
def api_user_create(body: UserCreate, principal=Depends(auth.require_ready_admin)):
    username = body.username.strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]{3,32}", username):
        raise HTTPException(400, "Identifiant : 3 à 32 caractères (lettres, chiffres, point, tiret).")
    password = body.password or auth.random_password()
    auth.check_password_strength(password)
    with db() as conn:
        if conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
            raise HTTPException(409, "Cet identifiant existe déjà.")
        conn.execute(
            "INSERT INTO users(username, display_name, pw_hash, role, must_change_pw, created_at)"
            " VALUES (?,?,?,?,1,?)",
            (username, body.display_name.strip() or username, auth.hash_password(password), body.role, now_iso()))
        audit(conn, principal.username, "utilisateur-cree", None, f"{username} ({body.role})")
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    return {"user": user_dict(row), "temporary_password": password}


def _other_admins(conn, user_id):
    return conn.execute("SELECT COUNT(*) FROM users WHERE role = 'admin' AND active = 1 AND id != ?",
                        (user_id,)).fetchone()[0]


@app.patch("/api/users/{user_id}")
def api_user_patch(user_id: int, body: UserPatch, principal=Depends(auth.require_ready_admin)):
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Utilisateur introuvable.")
        changes = []
        role = body.role if body.role is not None else row["role"]
        active = int(body.active) if body.active is not None else row["active"]
        if (role != "admin" or not active) and row["role"] == "admin" and _other_admins(conn, user_id) == 0:
            raise HTTPException(400, "Il doit rester au moins un administrateur actif.")
        if user_id == principal.id and (not active or role != "admin"):
            raise HTTPException(400, "Vous ne pouvez pas retirer vos propres droits.")
        name = body.display_name.strip() if body.display_name is not None else row["display_name"]
        conn.execute("UPDATE users SET display_name = ?, role = ?, active = ? WHERE id = ?",
                     (name, role, active, user_id))
        if not active:
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        if role != row["role"]:
            changes.append(f"rôle {row['role']} → {role}")
        if active != row["active"]:
            changes.append("activé" if active else "désactivé")
        audit(conn, principal.username, "utilisateur-modifie", None, f"{row['username']} : " + (", ".join(changes) or "nom"))
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return {"user": user_dict(row)}


@app.post("/api/users/{user_id}/reset-password")
def api_user_reset(user_id: int, principal=Depends(auth.require_ready_admin)):
    password = auth.random_password()
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Utilisateur introuvable.")
        conn.execute("UPDATE users SET pw_hash = ?, must_change_pw = 1, failed_logins = 0, locked_until = NULL"
                     " WHERE id = ?", (auth.hash_password(password), user_id))
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        audit(conn, principal.username, "mot-de-passe-reinitialise", None, row["username"])
    return {"temporary_password": password}


@app.delete("/api/users/{user_id}")
def api_user_delete(user_id: int, principal=Depends(auth.require_ready_admin)):
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Utilisateur introuvable.")
        if user_id == principal.id:
            raise HTTPException(400, "Vous ne pouvez pas supprimer votre propre compte.")
        if row["role"] == "admin" and _other_admins(conn, user_id) == 0:
            raise HTTPException(400, "Il doit rester au moins un administrateur actif.")
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        audit(conn, principal.username, "utilisateur-supprime", None, row["username"])
    return {"ok": True}


# =============================================================== biens et vues
STATE_GROUPS = ("live", "scheduled", "draft", "expired", "trash", "processing")


@app.get("/api/items")
def api_items(views: int = 0, state: str = "live", q: str = "", principal=Depends(auth.require_ready_user)):
    with db() as conn:
        settings = get_settings(conn)
        today = today_local(settings)
        rows = conn.execute("SELECT * FROM items ORDER BY updated_at DESC, id DESC").fetchall()
    counts = {g: 0 for g in STATE_GROUPS}
    result = []
    needle = q.strip().lower()
    for row in rows:
        is_view = row["kind"] == "view"
        if bool(views) != is_view:
            continue
        data = item_dict(row, today)
        group = "live" if data["state"] == "processing" else data["state"]
        counts[group] += 1
        if group != state:
            continue
        if needle and needle not in (row["title"] + " " + row["ref"]).lower():
            continue
        result.append(data)
    return {"items": result, "counts": counts}


@app.get("/api/items/{item_id}")
def api_item(item_id: int, principal=Depends(auth.require_ready_user)):
    with db() as conn:
        row = get_item_or_404(conn, item_id)
        settings = get_settings(conn)
        history = conn.execute(
            "SELECT ts, username, action, detail FROM audit WHERE item_id = ? ORDER BY id DESC LIMIT 15",
            (item_id,)).fetchall()
        data = item_dict(row, today_local(settings), detail=True)
        data["created_by"] = username_by_id(conn, row["created_by"]) if row["created_by"] else ""
        data["history"] = [dict(h) for h in history]
    return {"item": data}


@app.post("/api/items")
def api_item_create(file: UploadFile = File(...), title: str = Form(""), status: str = Form("active"),
                    principal=Depends(auth.require_ready_user)):
    tmp = save_upload(file)
    if status not in ("active", "draft"):
        status = "active"
    with db() as conn:
        settings = get_settings(conn)
        today = today_local(settings)
        cur = conn.execute(
            "INSERT INTO items(kind, title, status, weight, start_date, rev, created_by, created_at, updated_by,"
            " updated_at) VALUES ('poster', ?, ?, ?, ?, 1, ?, ?, ?, ?)",
            (title.strip() or Path(file.filename or "Nouveau bien").stem, status, int(settings["default_weight"]),
             today.isoformat(), principal.id, now_iso(), principal.id, now_iso()))
        item_id = cur.lastrowid
        audit(conn, principal.username, "bien-cree", item_id, file.filename or "")
    start_job(item_id, tmp, 1, principal.username, False, file.filename or "fichier")
    return {"id": item_id}


@app.post("/api/items/view")
def api_view_create(body: ViewCreate, principal=Depends(auth.require_ready_user)):
    data = clean_view_data(body.view_type, body.view_data)
    with db() as conn:
        settings = get_settings(conn)
        today = today_local(settings)
        titles = {"texte": "Annonce", "qr": "QR code", "agence": "Notre agence"}
        cur = conn.execute(
            "INSERT INTO items(kind, title, status, weight, start_date, rev, view_type, view_data, created_by,"
            " created_at, updated_by, updated_at) VALUES ('view', ?, 'active', ?, ?, 1, ?, ?, ?, ?, ?, ?)",
            (body.title.strip() or titles[body.view_type], int(settings["default_weight"]), today.isoformat(),
             body.view_type, json.dumps(data), principal.id, now_iso(), principal.id, now_iso()))
        item_id = cur.lastrowid
        data = finish_view(item_id, 1, body.view_type, data)
        conn.execute("UPDATE items SET view_data = ? WHERE id = ?", (json.dumps(data), item_id))
        audit(conn, principal.username, "vue-creee", item_id, body.view_type)
        row = get_item_or_404(conn, item_id)
        out = item_dict(row, today, detail=True)
    return {"item": out}


def clean_view_data(view_type, data):
    """Nettoie les champs d'une vue (texte uniquement, longueurs bornées)."""
    def s(key, n):
        return str(data.get(key, "") or "").strip()[:n]
    if view_type == "texte":
        theme = data.get("theme") if data.get("theme") in ("creme", "rouge", "vert", "sombre") else "creme"
        return {"heading": s("heading", 80), "text": s("text", 600), "theme": theme}
    if view_type == "qr":
        return {"heading": s("heading", 80), "caption": s("caption", 200), "url": s("url", 300)}
    return {"heading": s("heading", 80), "address": s("address", 200), "phone": s("phone", 40),
            "hours": s("hours", 300), "site": s("site", 100), "name": s("name", 80)}


def finish_view(item_id, rev, view_type, data):
    """Étapes après création/modification d'une vue : génération du QR code."""
    if view_type == "qr":
        if not data.get("url"):
            raise HTTPException(400, "Indiquez l'adresse (URL) du QR code.")
        out = media.item_dir(item_id, rev)
        out.mkdir(parents=True, exist_ok=True)
        media.make_qr(data["url"], out / "qr.svg")
        data["qr_src"] = media.url_for(item_id, rev, "qr.svg")
    return data


@app.patch("/api/items/{item_id}")
def api_item_patch(item_id: int, body: ItemPatch, principal=Depends(auth.require_ready_user)):
    fields = body.model_fields_set
    with db() as conn:
        row = get_item_or_404(conn, item_id)
        if row["status"] == "trash":
            raise HTTPException(400, "Ce bien est dans la corbeille : restaurez-le d'abord.")
        settings = get_settings(conn)
        today = today_local(settings)
        updates, changed = {}, []

        def put(column, value, label=None):
            if row[column] != value:
                updates[column] = value
                changed.append(label or column)

        if "title" in fields and body.title is not None:
            put("title", body.title.strip()[:200] or row["title"], "titre")
        if "ref" in fields and body.ref is not None:
            put("ref", body.ref.strip()[:60], "référence")
        if "notes" in fields and body.notes is not None:
            put("notes", body.notes.strip()[:1000], "notes")
        if "status" in fields and body.status:
            put("status", body.status, "statut")
        if "duration" in fields:
            if body.duration is not None and not 3 <= body.duration <= 600:
                raise HTTPException(400, "Durée : entre 3 et 600 secondes.")
            put("duration", body.duration, "durée")
        if "weight" in fields and body.weight is not None:
            if not 1 <= body.weight <= 5:
                raise HTTPException(400, "Importance : de 1 à 5.")
            put("weight", body.weight, "importance")
        if "start_date" in fields:
            put("start_date", valid_date(body.start_date, "début"), "début")
        if "end_date" in fields:
            put("end_date", valid_date(body.end_date, "fin"), "fin")
        start = updates.get("start_date", row["start_date"])
        end = updates.get("end_date", row["end_date"])
        if start and end and end < start:
            raise HTTPException(400, "La date de fin précède la date de début.")
        if "badge" in fields and body.badge is not None:
            if body.badge not in BADGES:
                raise HTTPException(400, "Pastille inconnue.")
            put("badge", body.badge, "pastille")
            if body.badge and "badge_until" not in fields and row["badge"] != body.badge:
                put("badge_until", add_days(today, int(settings["new_badge_days"])), "pastille jusqu'au")
            if not body.badge:
                put("badge_until", None)
        if "badge_until" in fields:
            put("badge_until", valid_date(body.badge_until, "pastille jusqu'au"), "pastille jusqu'au")
        if "screens" in fields and body.screens is not None:
            put("screens", normalise_screens(body.screens, conn), "écrans")
        if "price" in fields:
            if body.price is not None and body.price < 0:
                raise HTTPException(400, "Prix invalide.")
            put("price", body.price, "prix")
        if "view_data" in fields and body.view_data is not None and row["kind"] == "view":
            data = clean_view_data(row["view_type"], body.view_data)
            data = finish_view(item_id, row["rev"], row["view_type"], data)
            put("view_data", json.dumps(data), "contenu")
        if updates:
            updates["updated_at"] = now_iso()
            updates["updated_by"] = principal.id
            sets = ", ".join(f"{k} = ?" for k in updates)
            conn.execute(f"UPDATE items SET {sets} WHERE id = ?", (*updates.values(), item_id))
            audit(conn, principal.username, "bien-modifie", item_id, ", ".join(dict.fromkeys(changed)))
        row = get_item_or_404(conn, item_id)
        out = item_dict(row, today, detail=True)
    return {"item": out}


@app.post("/api/items/{item_id}/replace")
def api_item_replace(item_id: int, file: UploadFile = File(...), principal=Depends(auth.require_ready_user)):
    with db() as conn:
        row = get_item_or_404(conn, item_id)
        if row["kind"] == "view":
            raise HTTPException(400, "Une vue n'a pas de fichier à remplacer.")
        if row["status"] == "trash":
            raise HTTPException(400, "Ce bien est dans la corbeille.")
        new_rev = row["rev"] + 1
    with BUSY_LOCK:
        if item_id in BUSY:
            raise HTTPException(409, "Un traitement est déjà en cours pour ce bien.")
    tmp = save_upload(file)
    start_job(item_id, tmp, new_rev, principal.username, True, file.filename or "fichier")
    return {"ok": True}


@app.post("/api/items/{item_id}/trash")
def api_item_trash(item_id: int, principal=Depends(auth.require_ready_user)):
    with db() as conn:
        row = get_item_or_404(conn, item_id)
        conn.execute("UPDATE items SET status = 'trash', trashed_at = ?, updated_at = ?, updated_by = ? WHERE id = ?",
                     (now_iso(), now_iso(), principal.id, item_id))
        audit(conn, principal.username, "bien-corbeille", item_id, row["title"])
    return {"ok": True}


@app.post("/api/items/{item_id}/restore")
def api_item_restore(item_id: int, principal=Depends(auth.require_ready_user)):
    with db() as conn:
        row = get_item_or_404(conn, item_id)
        conn.execute("UPDATE items SET status = 'draft', trashed_at = NULL, updated_at = ?, updated_by = ? WHERE id = ?",
                     (now_iso(), principal.id, item_id))
        audit(conn, principal.username, "bien-restaure", item_id, row["title"])
    return {"ok": True}


@app.delete("/api/items/{item_id}")
def api_item_delete(item_id: int, principal=Depends(auth.require_ready_admin)):
    with db() as conn:
        row = get_item_or_404(conn, item_id)
        if row["status"] != "trash":
            raise HTTPException(400, "Mettez d'abord le bien à la corbeille.")
        conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
        audit(conn, principal.username, "bien-supprime", item_id, row["title"])
    media.delete_item_files(item_id)
    return {"ok": True}


# =============================================================== écrans
def screen_dict(row, now=None):
    now = now or utcnow()
    seen = parse_iso(row["last_seen"])
    seconds = int((now - seen).total_seconds()) if seen else None
    override_until = parse_iso(row["override_until"])
    paused_until = parse_iso(row["paused_until"])
    return {
        "id": row["id"], "slug": row["slug"], "name": row["name"], "orientation": row["orientation"],
        "order_mode": row["order_mode"], "default_duration": row["default_duration"],
        "weather_every": row["weather_every"], "weather_seconds": row["weather_seconds"],
        "corner_clock": bool(row["corner_clock"]), "fit": row["fit"], "ticker": row["ticker"],
        "open_time": row["open_time"], "close_time": row["close_time"],
        "hours_enabled": bool(row["hours_enabled"]),
        "last_seen": row["last_seen"], "last_ip": row["last_ip"],
        "online": seconds is not None and seconds <= ONLINE_SECONDS, "seconds_since": seconds,
        "override": {"text": row["override_text"], "until": row["override_until"]}
        if override_until and override_until > now else None,
        "paused_until": row["paused_until"] if paused_until and paused_until > now else None,
    }


@app.get("/api/screens")
def api_screens(principal=Depends(auth.require_ready_user)):
    with db() as conn:
        rows = conn.execute("SELECT * FROM screens ORDER BY id").fetchall()
        default = get_settings(conn)["default_screen"]
    return {"screens": [screen_dict(r) for r in rows], "default_screen": default}


@app.post("/api/screens")
def api_screen_create(body: ScreenCreate, principal=Depends(auth.require_ready_admin)):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Donnez un nom à l'écran.")
    plain = unicodedata.normalize("NFKD", name.lower()).encode("ascii", "ignore").decode()
    slug = body.slug.strip().lower() or re.sub(r"[^a-z0-9]+", "-", plain).strip("-")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,31}", slug) or slug == "default":
        raise HTTPException(400, "Identifiant d'écran : 2 à 32 caractères (lettres minuscules, chiffres, tirets).")
    with db() as conn:
        if conn.execute("SELECT 1 FROM screens WHERE slug = ?", (slug,)).fetchone():
            raise HTTPException(409, "Un écran porte déjà cet identifiant.")
        settings = get_settings(conn)
        conn.execute(
            "INSERT INTO screens(slug, name, orientation, default_duration, weather_every, weather_seconds, created_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (slug, name, body.orientation, int(settings["default_duration"]), CFG.weather_every,
             CFG.weather_seconds, now_iso()))
        audit(conn, principal.username, "ecran-cree", None, f"{name} ({slug})")
        row = conn.execute("SELECT * FROM screens WHERE slug = ?", (slug,)).fetchone()
    return {"screen": screen_dict(row)}


@app.patch("/api/screens/{screen_id}")
def api_screen_patch(screen_id: int, body: ScreenPatch, principal=Depends(auth.require_ready_admin)):
    fields = body.model_fields_set
    with db() as conn:
        row = conn.execute("SELECT * FROM screens WHERE id = ?", (screen_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Écran introuvable.")
        updates = {}
        for key in ("name", "orientation", "order_mode", "fit"):
            if key in fields and getattr(body, key) is not None:
                updates[key] = getattr(body, key).strip() if key == "name" else getattr(body, key)
        if updates.get("name") == "":
            raise HTTPException(400, "Le nom ne peut pas être vide.")
        if "default_duration" in fields and body.default_duration is not None:
            if not 3 <= body.default_duration <= 600:
                raise HTTPException(400, "Durée par défaut : entre 3 et 600 secondes.")
            updates["default_duration"] = body.default_duration
        if "weather_every" in fields and body.weather_every is not None:
            if not 0 <= body.weather_every <= 50:
                raise HTTPException(400, "Météo : toutes les 0 à 50 diapositives (0 = jamais).")
            updates["weather_every"] = body.weather_every
        if "weather_seconds" in fields and body.weather_seconds is not None:
            if not 3 <= body.weather_seconds <= 120:
                raise HTTPException(400, "Durée de la météo : entre 3 et 120 secondes.")
            updates["weather_seconds"] = body.weather_seconds
        if "corner_clock" in fields and body.corner_clock is not None:
            updates["corner_clock"] = int(body.corner_clock)
        if "ticker" in fields and body.ticker is not None:
            updates["ticker"] = "\n".join(l.strip() for l in body.ticker.splitlines() if l.strip())[:1000]
        if "open_time" in fields and body.open_time is not None:
            updates["open_time"] = valid_hhmm(body.open_time, "ouverture")
        if "close_time" in fields and body.close_time is not None:
            updates["close_time"] = valid_hhmm(body.close_time, "fermeture")
        if "hours_enabled" in fields and body.hours_enabled is not None:
            updates["hours_enabled"] = int(body.hours_enabled)
        if updates:
            sets = ", ".join(f"{k} = ?" for k in updates)
            conn.execute(f"UPDATE screens SET {sets} WHERE id = ?", (*updates.values(), screen_id))
            audit(conn, principal.username, "ecran-modifie", None, f"{row['name']} : " + ", ".join(updates))
        row = conn.execute("SELECT * FROM screens WHERE id = ?", (screen_id,)).fetchone()
    return {"screen": screen_dict(row)}


@app.delete("/api/screens/{screen_id}")
def api_screen_delete(screen_id: int, principal=Depends(auth.require_ready_admin)):
    with db() as conn:
        row = conn.execute("SELECT * FROM screens WHERE id = ?", (screen_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Écran introuvable.")
        settings = get_settings(conn)
        if conn.execute("SELECT COUNT(*) FROM screens").fetchone()[0] <= 1:
            raise HTTPException(400, "Il faut garder au moins un écran.")
        if row["slug"] == settings["default_screen"]:
            raise HTTPException(400, "C'est l'écran par défaut : changez d'abord l'écran par défaut dans les Réglages.")
        conn.execute("DELETE FROM screens WHERE id = ?", (screen_id,))
        # les biens ciblant uniquement cet écran sont désormais sans écran : on les repasse sur « tous »
        for item in conn.execute("SELECT id, screens FROM items WHERE screens != '*'").fetchall():
            slugs = [s for s in jloads(item["screens"], []) if s != row["slug"]]
            conn.execute("UPDATE items SET screens = ? WHERE id = ?", (json.dumps(slugs) if slugs else "*", item["id"]))
        audit(conn, principal.username, "ecran-supprime", None, row["name"])
    return {"ok": True}


def _screen_or_404(conn, screen_id):
    row = conn.execute("SELECT * FROM screens WHERE id = ?", (screen_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Écran introuvable.")
    return row


@app.post("/api/screens/{screen_id}/override")
def api_override(screen_id: int, body: OverrideBody, principal=Depends(auth.require_ready_user)):
    text = body.text.strip()[:300]
    if not text:
        raise HTTPException(400, "Saisissez le message à afficher.")
    if not 1 <= body.minutes <= 24 * 60 * 7:
        raise HTTPException(400, "Durée : de 1 minute à 7 jours.")
    with db() as conn:
        row = _screen_or_404(conn, screen_id)
        until = utcnow() + timedelta(minutes=body.minutes)
        conn.execute("UPDATE screens SET override_text = ?, override_until = ? WHERE id = ?",
                     (text, iso(until), screen_id))
        audit(conn, principal.username, "message-urgent", None, f"{row['name']} : {text}")
    return {"ok": True}


@app.delete("/api/screens/{screen_id}/override")
def api_override_clear(screen_id: int, principal=Depends(auth.require_ready_user)):
    with db() as conn:
        row = _screen_or_404(conn, screen_id)
        conn.execute("UPDATE screens SET override_text = NULL, override_until = NULL WHERE id = ?", (screen_id,))
        audit(conn, principal.username, "message-urgent-retire", None, row["name"])
    return {"ok": True}


@app.post("/api/screens/{screen_id}/pause")
def api_pause(screen_id: int, body: PauseBody, principal=Depends(auth.require_ready_user)):
    if not 1 <= body.minutes <= 24 * 60:
        raise HTTPException(400, "Durée : de 1 minute à 24 heures.")
    with db() as conn:
        row = _screen_or_404(conn, screen_id)
        conn.execute("UPDATE screens SET paused_until = ? WHERE id = ?",
                     (iso(utcnow() + timedelta(minutes=body.minutes)), screen_id))
        audit(conn, principal.username, "pause", None, f"{row['name']} : {body.minutes} min")
    return {"ok": True}


@app.delete("/api/screens/{screen_id}/pause")
def api_resume(screen_id: int, principal=Depends(auth.require_ready_user)):
    with db() as conn:
        row = _screen_or_404(conn, screen_id)
        conn.execute("UPDATE screens SET paused_until = NULL WHERE id = ?", (screen_id,))
        audit(conn, principal.username, "reprise", None, row["name"])
    return {"ok": True}


# =============================================================== réglages, historique, système
SETTINGS_PUBLIC = ["agency_name", "agency_address", "agency_phone", "agency_hours", "agency_site", "city",
                   "latitude", "longitude", "timezone", "default_duration", "default_weight", "new_badge_days",
                   "default_screen", "alerts_enabled", "alert_to", "smtp_host", "smtp_port", "smtp_security",
                   "smtp_user", "smtp_from"]


def settings_out(settings):
    out = {k: settings[k] for k in SETTINGS_PUBLIC}
    out["alerts_enabled"] = settings["alerts_enabled"] == "1"
    out["smtp_password_set"] = bool(settings["smtp_password"])
    return out


@app.get("/api/settings")
def api_settings(principal=Depends(auth.require_ready_user)):
    with db() as conn:
        settings = get_settings(conn)
    data = settings_out(settings)
    if not principal.is_admin:  # les utilisateurs n'ont besoin que des valeurs par défaut
        data = {k: data[k] for k in ("agency_name", "default_duration", "default_weight", "new_badge_days", "city")}
    return {"settings": data}


@app.put("/api/settings")
def api_settings_put(body: SettingsBody, principal=Depends(auth.require_ready_admin)):
    values = {}
    for key, value in body.model_dump(exclude_unset=True).items():
        if value is None:
            continue
        if key == "alerts_enabled":
            value = "1" if value else "0"
        if key == "smtp_password" and value == "":
            continue  # champ laissé vide : on conserve l'ancien mot de passe
        values[key] = str(value).strip() if isinstance(value, str) else value
    with db() as conn:
        if "timezone" in values:
            from zoneinfo import ZoneInfo
            try:
                ZoneInfo(values["timezone"])
            except Exception:  # noqa: BLE001
                raise HTTPException(400, "Fuseau horaire inconnu (exemple : Europe/Paris).")
        for key in ("latitude", "longitude"):
            if key in values:
                try:
                    float(values[key])
                except ValueError:
                    raise HTTPException(400, "Latitude/longitude : nombres décimaux (exemple : 47.2978).")
        if "default_duration" in values and not 3 <= int(values["default_duration"]) <= 600:
            raise HTTPException(400, "Durée par défaut : entre 3 et 600 secondes.")
        if "default_weight" in values and not 1 <= int(values["default_weight"]) <= 5:
            raise HTTPException(400, "Importance par défaut : de 1 à 5.")
        if "default_screen" in values and values["default_screen"] not in screens_slugs(conn):
            raise HTTPException(400, "Écran par défaut inconnu.")
        set_settings(conn, values)
        shown = ", ".join(k for k in values if k != "smtp_password") + (" (mot de passe SMTP)" if "smtp_password" in values else "")
        audit(conn, principal.username, "reglages-modifies", None, shown)
        settings = get_settings(conn)
    return {"settings": settings_out(settings)}


@app.post("/api/settings/test-email")
def api_test_email(principal=Depends(auth.require_ready_admin)):
    try:
        recipient = monitor.send_mail("Test de la vitrine", "Ce message confirme que les alertes par e-mail fonctionnent.")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"Envoi impossible : {exc}")
    return {"sent_to": recipient}


@app.get("/api/audit")
def api_audit(limit: int = 200, principal=Depends(auth.require_ready_admin)):
    with db() as conn:
        rows = conn.execute("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (min(max(limit, 1), 1000),)).fetchall()
    return {"audit": [dict(r) for r in rows]}


@app.get("/api/system")
def api_system(principal=Depends(auth.require_ready_user)):
    disk = shutil.disk_usage(CFG.data_dir)
    uptime = None
    try:
        uptime = int(float(Path("/proc/uptime").read_text().split()[0]))
    except (OSError, ValueError):
        pass
    tools = {name: bool(shutil.which(name)) for name in ("ffmpeg", "pdftoppm", "pdftotext")}
    return {"version": VERSION, "temperature": monitor.TEMP.snapshot(), "uptime_seconds": uptime,
            "disk_free_gb": round(disk.free / 1e9, 1), "disk_total_gb": round(disk.total / 1e9, 1),
            "tools": tools, "time": now_iso()}


@app.get("/api/backup")
def api_backup(principal=Depends(auth.require_ready_admin)):
    path = monitor.make_backup("manuel")
    with db() as conn:
        audit(conn, principal.username, "sauvegarde-telechargee")
    return FileResponse(path, media_type="application/octet-stream", filename=path.name)


# =============================================================== API des écrans (publique, en lecture)
def resolve_screen(conn, slug):
    settings = get_settings(conn)
    if slug == "default":
        slug = settings["default_screen"]
    row = conn.execute("SELECT * FROM screens WHERE slug = ?", (slug,)).fetchone()
    if not row:
        raise HTTPException(404, "Écran inconnu.")
    return row, settings


def agency_view(settings, data):
    merged = {
        "heading": data.get("heading") or "Notre agence",
        "name": data.get("name") or settings["agency_name"],
        "address": data.get("address") or settings["agency_address"],
        "phone": data.get("phone") or settings["agency_phone"],
        "hours": data.get("hours") or settings["agency_hours"],
        "site": data.get("site") or settings["agency_site"],
    }
    return merged


def make_unit(row, screen, settings, today):
    """Convertit un bien en « contenu » prêt à être diffusé (None si rien à afficher)."""
    slides = jloads(row["slides"], [])
    badge = effective_badge(row, today)
    unit = {"id": row["id"], "kind": row["kind"], "title": row["title"], "weight": row["weight"],
            "duration": row["duration"] or screen["default_duration"], "badge": badge,
            "badge_label": BADGES.get(badge, "")}
    if row["kind"] == "view":
        data = jloads(row["view_data"], {})
        if row["view_type"] == "agence":
            data = agency_view(settings, data)
        unit["view"] = {"type": row["view_type"], "data": data}
        unit["slides"] = []
    else:
        if not slides:
            return None
        unit["slides"] = slides
    return unit


@app.get("/api/player/{slug}/playlist")
def api_player_playlist(slug: str, request: Request, preview: int = 0, only: Optional[int] = None):
    if only is not None:
        auth.current_principal(request)  # l'aperçu d'un bien précis exige d'être connecté
        preview = 1
    with db() as conn:
        screen, settings = resolve_screen(conn, slug)
        now = utcnow()
        if not preview:
            conn.execute("UPDATE screens SET last_seen = ?, last_ip = ?, alerted = 0 WHERE id = ?",
                         (now_iso(), request.client.host if request.client else "", screen["id"]))
        today = today_local(settings)
        if only is not None:
            rows = [get_item_or_404(conn, only)]
        else:
            rows = conn.execute("SELECT * FROM items WHERE status = 'active' ORDER BY created_at DESC, id DESC").fetchall()
    units = []
    for row in rows:
        if only is None:
            if row["start_date"] and row["start_date"] > today.isoformat():
                continue
            if row["end_date"] and row["end_date"] < today.isoformat():
                continue
            targets = screens_list(row["screens"])
            if targets != "*" and screen["slug"] not in targets:
                continue
        unit = make_unit(row, screen, settings, today)
        if unit:
            units.append(unit)
    override = None
    until = parse_iso(screen["override_until"])
    if only is None and until and until > now and screen["override_text"]:
        override = {"text": screen["override_text"], "until": screen["override_until"]}
    paused = only is None and bool(parse_iso(screen["paused_until"]) and parse_iso(screen["paused_until"]) > now)
    payload = {
        "screen": {"slug": screen["slug"], "name": screen["name"], "orientation": screen["orientation"],
                   "order_mode": screen["order_mode"],
                   "weather_every": 0 if only is not None else screen["weather_every"],
                   "weather_seconds": screen["weather_seconds"], "corner_clock": bool(screen["corner_clock"]) and only is None,
                   "fit": screen["fit"], "default_duration": screen["default_duration"],
                   # l'aperçu ignore les heures d'ouverture : on doit pouvoir le consulter à tout moment
                   "hours_enabled": bool(screen["hours_enabled"]) and not preview,
                   "open_time": screen["open_time"], "close_time": screen["close_time"],
                   "ticker": [] if only is not None else [l for l in screen["ticker"].splitlines() if l.strip()]},
        "site": {"city": settings["city"], "latitude": float(settings["latitude"]),
                 "longitude": float(settings["longitude"]), "timezone": settings["timezone"]},
        "items": units, "override": override, "paused": paused,
    }
    payload["version"] = hashlib.sha1(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:12]
    payload["server_time"] = int(time.time() * 1000)
    payload["app_version"] = VERSION
    return JSONResponse(payload, headers={"Cache-Control": "no-store"})


@app.get("/api/status")
def api_status():
    """État minimal, sans authentification (supervision locale)."""
    with db() as conn:
        screens = conn.execute("SELECT slug, last_seen FROM screens").fetchall()
        items = conn.execute("SELECT COUNT(*) FROM items WHERE status = 'active'").fetchone()[0]
    return {"ok": True, "version": VERSION, "items": items, "temperature": monitor.TEMP.snapshot(),
            "screens": {s["slug"]: s["last_seen"] for s in screens}}


# =============================================================== fichiers statiques
@app.get("/admin")
def admin_redirect():
    return RedirectResponse("/admin/")


CFG.ensure_dirs()
app.mount("/files", StaticFiles(directory=str(CFG.media_dir)), name="files")
app.mount("/admin", StaticFiles(directory=str(CFG.web_dir), html=True), name="admin")
app.mount("/", StaticFiles(directory=str(CFG.player_dir), html=True), name="player")
