"""Comptes, mots de passe, sessions."""
import base64
import hashlib
import hmac
import secrets
from datetime import timedelta

from fastapi import Depends, HTTPException, Request

from .config import CFG
from .db import db, iso, now_iso, parse_iso, utcnow

COOKIE = "vitrine_session"
MIN_PASSWORD = 8
MAX_FAILS = 5
LOCK_MINUTES = 10

_SCRYPT = dict(n=2 ** 14, r=8, p=1, dklen=32)


def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, maxmem=64 * 1024 * 1024, **_SCRYPT)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def verify_password(password, stored):
    try:
        scheme, salt_b64, digest_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
        digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, maxmem=64 * 1024 * 1024, **_SCRYPT)
        return hmac.compare_digest(digest, expected)
    except Exception:  # noqa: BLE001
        return False


def random_password(length=12):
    """Mot de passe temporaire lisible (sans caractères ambigus)."""
    alphabet = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def check_password_strength(password):
    if len(password) < MIN_PASSWORD:
        raise HTTPException(400, f"Le mot de passe doit contenir au moins {MIN_PASSWORD} caractères.")


def _token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(conn, user_id):
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(24)
    now = utcnow()
    conn.execute(
        "INSERT INTO sessions(token_hash, user_id, csrf, created_at, expires_at) VALUES (?,?,?,?,?)",
        (_token_hash(token), user_id, csrf, iso(now), iso(now + timedelta(hours=CFG.session_hours))))
    conn.execute("DELETE FROM sessions WHERE expires_at < ?", (iso(now),))
    return token, csrf


def destroy_session(conn, token):
    conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))


class Principal:
    """Utilisateur connecté, tel qu'il est vu par les routes."""

    def __init__(self, row, csrf):
        self.id = row["id"]
        self.username = row["username"]
        self.display_name = row["display_name"] or row["username"]
        self.role = row["role"]
        self.must_change_pw = bool(row["must_change_pw"])
        self.csrf = csrf

    @property
    def is_admin(self):
        return self.role == "admin"

    def public(self):
        return {"id": self.id, "username": self.username, "display_name": self.display_name,
                "role": self.role, "must_change_pw": self.must_change_pw}


def current_principal(request: Request):
    token = request.cookies.get(COOKIE)
    if not token:
        raise HTTPException(401, "Connexion requise.")
    with db() as conn:
        row = conn.execute(
            "SELECT u.*, s.csrf, s.expires_at FROM sessions s JOIN users u ON u.id = s.user_id"
            " WHERE s.token_hash = ?", (_token_hash(token),)).fetchone()
        if not row or not row["active"]:
            raise HTTPException(401, "Session expirée.")
        expires = parse_iso(row["expires_at"])
        if not expires or expires < utcnow():
            raise HTTPException(401, "Session expirée.")
        return Principal(row, row["csrf"])


def require_user(request: Request, principal: Principal = Depends(current_principal)):
    """Utilisateur connecté ; les requêtes qui modifient des données doivent porter le jeton CSRF."""
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        sent = request.headers.get("x-csrf-token", "")
        if not hmac.compare_digest(sent, principal.csrf):
            raise HTTPException(403, "Requête refusée (jeton de sécurité manquant). Rechargez la page.")
    return principal


def require_admin(principal: Principal = Depends(require_user)):
    if not principal.is_admin:
        raise HTTPException(403, "Réservé à l'administrateur.")
    return principal


def require_ready_user(principal: Principal = Depends(require_user)):
    """Comme require_user, mais refuse tant que le mot de passe temporaire n'a pas été changé."""
    if principal.must_change_pw:
        raise HTTPException(403, "Vous devez d'abord changer votre mot de passe.")
    return principal


def require_ready_admin(principal: Principal = Depends(require_ready_user)):
    if not principal.is_admin:
        raise HTTPException(403, "Réservé à l'administrateur.")
    return principal


def login(conn, username, password, ip=""):
    """Vérifie les identifiants, gère le blocage temporaire. Retourne la ligne utilisateur ou lève 401/429."""
    row = conn.execute("SELECT * FROM users WHERE username = ?", (username.strip(),)).fetchone()
    now = utcnow()
    if row:
        locked = parse_iso(row["locked_until"])
        if locked and locked > now:
            minutes = int((locked - now).total_seconds() // 60) + 1
            raise HTTPException(429, f"Trop d'essais. Réessayez dans {minutes} minute(s).")
    ok = bool(row) and bool(row["active"]) and verify_password(password, row["pw_hash"])
    if not ok:
        if row:
            fails = row["failed_logins"] + 1
            locked_until = iso(now + timedelta(minutes=LOCK_MINUTES)) if fails >= MAX_FAILS else None
            conn.execute("UPDATE users SET failed_logins = ?, locked_until = ? WHERE id = ?",
                         (0 if locked_until else fails, locked_until, row["id"]))
        else:
            hash_password("x")  # temps de réponse comparable
        raise HTTPException(401, "Identifiant ou mot de passe incorrect.")
    conn.execute("UPDATE users SET failed_logins = 0, locked_until = NULL, last_login = ? WHERE id = ?",
                 (now_iso(), row["id"]))
    return row
