"""Reprise des fichiers de l'ancien dossier synchronisé (data/inbox) à la première mise à jour."""
import json
import logging
import re
from datetime import date
from pathlib import Path

from . import media
from .config import CFG
from .db import audit, db, get_settings, now_iso, set_settings, today_local

log = logging.getLogger("vitrine.importer")

TAG_RE = re.compile(r"\[(duree|debut|fin)=([^\]]+)\]", re.IGNORECASE)


def parse_tags(stem):
    tags = {}
    for key, value in TAG_RE.findall(stem):
        key, value = key.lower(), value.strip()
        try:
            if key == "duree":
                tags["duration"] = max(3, min(600, int(value)))
            else:
                tags["start_date" if key == "debut" else "end_date"] = date.fromisoformat(value).isoformat()
        except ValueError:
            continue
    return tags


def clean_title(stem):
    title = TAG_RE.sub("", stem).strip(" -_")
    title = re.sub(r"^\d+[\s._-]+", "", title)  # préfixe d'ordre « 01- »
    return title or stem


def import_inbox(owner="import"):
    """Crée un bien par fichier de l'ancien dossier. Retourne le nombre de biens créés."""
    inbox = CFG.inbox_dir
    if not inbox.is_dir():
        return 0
    files = sorted((p for p in inbox.rglob("*") if p.is_file() and not p.name.startswith(".")),
                   key=lambda p: media.natural_key(p.relative_to(inbox).as_posix()))
    created = 0
    with db() as conn:
        settings = get_settings(conn)
        today = today_local(settings)
        messages = None
        for path in files:
            if path.name.lower() == "messages.txt":
                lines = [l.strip() for l in path.read_text(encoding="utf-8", errors="replace").splitlines()
                         if l.strip() and not l.strip().startswith("#")]
                messages = "\n".join(lines)
                continue
            if path.suffix.lower() not in media.ALLOWED_EXT:
                continue
            tags = parse_tags(path.stem)
            cur = conn.execute(
                "INSERT INTO items(kind, title, status, duration, weight, start_date, end_date, rev, created_at,"
                " updated_at) VALUES ('poster', ?, 'active', ?, ?, ?, ?, 1, ?, ?)",
                (clean_title(path.stem), tags.get("duration"), int(settings["default_weight"]),
                 tags.get("start_date", today.isoformat()), tags.get("end_date"), now_iso(), now_iso()))
            item_id = cur.lastrowid
            try:
                kind, slides, thumb, info = media.process_file(path, item_id, 1)
            except Exception as exc:  # noqa: BLE001
                log.warning("import de %s impossible : %s", path.name, exc)
                conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
                continue
            title = info.get("title") or clean_title(path.stem)
            conn.execute("UPDATE items SET kind = ?, slides = ?, thumb = ?, title = ?, price = ? WHERE id = ?",
                         (kind, json.dumps(slides), thumb, title, info.get("price"), item_id))
            audit(conn, owner, "bien-importe", item_id, path.name)
            created += 1
        if messages:
            default = settings["default_screen"]
            conn.execute("UPDATE screens SET ticker = ? WHERE slug = ?", (messages, default))
    return created


def import_inbox_once():
    with db() as conn:
        settings = get_settings(conn)
        if settings["inbox_imported"] == "1":
            return
        has_items = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] > 0
    created = 0
    if not has_items:
        created = import_inbox()
        if created:
            log.info("%d fichier(s) de l'ancien dossier repris dans la bibliothèque", created)
    with db() as conn:
        set_settings(conn, {"inbox_imported": "1"})
