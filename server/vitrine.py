#!/usr/bin/env python3
"""Serveur de la vitrine : synchronise le dossier Drive, prépare la playlist
et sert le player (page web affichée en plein écran sur l'écran DynaScan).

Aucune dépendance Python : bibliothèque standard uniquement.
Outils externes (optionnels) : rclone (synchro Drive), pdftoppm (PDF -> images).
"""
import hashlib
import json
import logging
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import date, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parent.parent
PLAYER_DIR = ROOT / "player"

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
VIDEO_EXT = {".mp4", ".m4v", ".webm", ".mov"}
PDF_EXT = {".pdf"}
TICKER_FILE = "messages.txt"

log = logging.getLogger("vitrine")


def load_env_file(path):
    """Charge un fichier KEY=value sans écraser les variables déjà définies."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env_file(ROOT / "config.env")


def env(name, default):
    return os.environ.get(name, default)


class Config:
    def __init__(self):
        self.data_dir = Path(env("VITRINE_DATA_DIR", str(ROOT / "data"))).resolve()
        self.inbox = self.data_dir / "inbox"
        self.cache = self.data_dir / "cache"
        self.host = env("VITRINE_HOST", "127.0.0.1")
        self.port = int(env("VITRINE_PORT", "8080"))
        self.rclone_remote = env("RCLONE_REMOTE", "vitrine:")
        self.sync_enabled = env("SYNC_ENABLED", "1") == "1"
        self.sync_interval = int(env("SYNC_INTERVAL", "300"))
        self.image_seconds = int(env("IMAGE_SECONDS", "10"))
        self.pdf_dpi_width = int(env("PDF_WIDTH_PX", "1080"))
        self.city = env("CITY", "Carquefou")
        self.latitude = float(env("LATITUDE", "47.2978"))
        self.longitude = float(env("LONGITUDE", "-1.4903"))
        self.timezone = env("TIMEZONE", "Europe/Paris")
        self.widgets = env("WIDGETS", "1") == "1"


CFG = Config()


# --------------------------------------------------------------------------
# Synchronisation Drive
# --------------------------------------------------------------------------
class SyncState:
    def __init__(self):
        self.lock = threading.Lock()
        self.last_ok = None
        self.last_error = None
        self.last_attempt = None

    def snapshot(self):
        with self.lock:
            return {
                "last_ok": self.last_ok,
                "last_error": self.last_error,
                "last_attempt": self.last_attempt,
            }


SYNC = SyncState()


def sync_once():
    """Copie le dossier Drive vers data/inbox (rclone sync, lecture seule côté Drive)."""
    if not shutil.which("rclone"):
        raise RuntimeError("rclone n'est pas installé")
    CFG.inbox.mkdir(parents=True, exist_ok=True)
    cmd = [
        "rclone", "sync", CFG.rclone_remote, str(CFG.inbox),
        # Google Docs / Sheets / Slides sont exportés en PDF (puis découpés en images)
        "--drive-export-formats", "pdf",
        "--drive-skip-gdocs=false",
        "--fast-list",
        "--max-delete", "100",
        "--timeout", "60s",
        "--retries", "2",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip()[-500:])


def sync_loop():
    while True:
        with SYNC.lock:
            SYNC.last_attempt = datetime.now().isoformat(timespec="seconds")
        try:
            sync_once()
            with SYNC.lock:
                SYNC.last_ok = datetime.now().isoformat(timespec="seconds")
                SYNC.last_error = None
            log.info("synchronisation Drive terminée")
        except Exception as exc:  # noqa: BLE001 - on continue de diffuser la copie locale
            with SYNC.lock:
                SYNC.last_error = str(exc)
            log.warning("synchronisation Drive impossible : %s", exc)
        time.sleep(CFG.sync_interval)


# --------------------------------------------------------------------------
# Playlist
# --------------------------------------------------------------------------
TAG_RE = re.compile(r"\[(duree|debut|fin)=([^\]]+)\]", re.IGNORECASE)


def natural_key(text):
    return [int(p) if p.isdigit() else p.lower() for p in re.split(r"(\d+)", text)]


def parse_tags(stem):
    """Lit les options dans le nom : [duree=15] [debut=2026-11-01] [fin=2026-11-30]."""
    tags = {}
    for key, value in TAG_RE.findall(stem):
        key = key.lower()
        value = value.strip()
        try:
            if key == "duree":
                tags["duration"] = max(1, int(value))
            else:
                tags["start" if key == "debut" else "end"] = date.fromisoformat(value)
        except ValueError:
            log.warning("option ignorée dans %r : %s=%s", stem, key, value)
    return tags


def pdf_to_images(pdf_path, rel):
    """Convertit un PDF en PNG (une image par page), avec cache."""
    stat = pdf_path.stat()
    key = hashlib.sha1(f"{rel}|{stat.st_mtime_ns}|{stat.st_size}".encode()).hexdigest()[:16]
    out_dir = CFG.cache / key
    if not out_dir.is_dir():
        if not shutil.which("pdftoppm"):
            log.warning("pdftoppm manquant : %s ignoré", rel)
            return []
        tmp = CFG.cache / f"{key}.tmp"
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        cmd = ["pdftoppm", "-png", "-scale-to-x", str(CFG.pdf_dpi_width), "-scale-to-y", "-1",
               str(pdf_path), str(tmp / "page")]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=300)
        except (subprocess.SubprocessError, OSError) as exc:
            log.warning("conversion PDF impossible pour %s : %s", rel, exc)
            shutil.rmtree(tmp, ignore_errors=True)
            return []
        tmp.rename(out_dir)
    return sorted(out_dir.glob("page-*.png"), key=lambda p: natural_key(p.name)), key


def scan_media():
    """Parcourt data/inbox et renvoie (items, messages)."""
    items = []
    messages = []
    used_cache = set()
    if not CFG.inbox.is_dir():
        return items, messages
    files = sorted(
        (p for p in CFG.inbox.rglob("*") if p.is_file() and not p.name.startswith(".")),
        key=lambda p: natural_key(p.relative_to(CFG.inbox).as_posix()),
    )
    for path in files:
        rel = path.relative_to(CFG.inbox).as_posix()
        ext = path.suffix.lower()
        tags = parse_tags(path.stem)
        if path.name.lower() == TICKER_FILE:
            messages = [l.strip() for l in path.read_text(encoding="utf-8", errors="replace").splitlines()
                        if l.strip() and not l.strip().startswith("#")]
            continue
        base = {"start": tags.get("start"), "end": tags.get("end")}
        if ext in IMAGE_EXT:
            items.append({**base, "type": "image", "src": "/media/" + rel,
                          "duration": tags.get("duration", CFG.image_seconds), "name": rel,
                          "sig": f"{rel}|{path.stat().st_mtime_ns}"})
        elif ext in VIDEO_EXT:
            items.append({**base, "type": "video", "src": "/media/" + rel, "name": rel,
                          "sig": f"{rel}|{path.stat().st_mtime_ns}"})
        elif ext in PDF_EXT:
            converted = pdf_to_images(path, rel)
            if not converted:
                continue
            pages, key = converted
            used_cache.add(key)
            for page in pages:
                items.append({**base, "type": "image", "src": f"/cache/{key}/{page.name}",
                              "duration": tags.get("duration", CFG.image_seconds),
                              "name": f"{rel} ({page.stem})", "sig": f"{key}/{page.name}"})
    # nettoyage du cache des PDF disparus
    if CFG.cache.is_dir():
        for entry in CFG.cache.iterdir():
            if entry.is_dir() and entry.name not in used_cache and not entry.name.endswith(".tmp"):
                shutil.rmtree(entry, ignore_errors=True)
    return items, messages


class PlaylistCache:
    """Évite de rescanner le disque à chaque requête du player."""

    def __init__(self):
        self.lock = threading.Lock()
        self.items = []
        self.messages = []
        self.fingerprint = None

    def refresh(self):
        fingerprint = self._fingerprint()
        with self.lock:
            if fingerprint == self.fingerprint:
                return
        items, messages = scan_media()
        with self.lock:
            self.items, self.messages, self.fingerprint = items, messages, fingerprint
        log.info("playlist mise à jour : %d éléments", len(items))

    @staticmethod
    def _fingerprint():
        if not CFG.inbox.is_dir():
            return ()
        return tuple(sorted(
            (p.relative_to(CFG.inbox).as_posix(), p.stat().st_mtime_ns, p.stat().st_size)
            for p in CFG.inbox.rglob("*") if p.is_file()
        ))

    def current(self):
        with self.lock:
            items, messages = list(self.items), list(self.messages)
        today = date.today()
        active = [i for i in items
                  if (i["start"] is None or i["start"] <= today) and (i["end"] is None or today <= i["end"])]
        public = [{k: v for k, v in i.items() if k in ("type", "src", "duration", "name")} for i in active]
        version = hashlib.sha1(json.dumps([i["sig"] for i in active] + messages).encode()).hexdigest()[:12]
        return {
            "version": version,
            "items": public,
            "messages": messages,
            "config": {
                "city": CFG.city, "latitude": CFG.latitude, "longitude": CFG.longitude,
                "timezone": CFG.timezone, "widgets": CFG.widgets,
            },
        }


PLAYLIST = PlaylistCache()


def playlist_refresh_loop():
    while True:
        try:
            PLAYLIST.refresh()
        except Exception:  # noqa: BLE001
            log.exception("échec de la mise à jour de la playlist")
        time.sleep(20)


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "Vitrine/1.0"

    def log_message(self, fmt, *args):  # silence les logs d'accès
        log.debug("%s - %s", self.address_string(), fmt % args)

    def do_GET(self):
        path = unquote(urlparse(self.path).path)
        if path == "/api/playlist":
            return self.send_json(PLAYLIST.current())
        if path == "/api/status":
            data = PLAYLIST.current()
            return self.send_json({"ok": True, "items": len(data["items"]),
                                   "sync": SYNC.snapshot(), "sync_enabled": CFG.sync_enabled})
        if path.startswith("/media/"):
            return self.send_file(CFG.inbox, path[len("/media/"):])
        if path.startswith("/cache/"):
            return self.send_file(CFG.cache, path[len("/cache/"):])
        if path == "/":
            path = "/index.html"
        return self.send_file(PLAYER_DIR, path.lstrip("/"))

    def send_json(self, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, base, rel):
        base = base.resolve()
        target = (base / rel).resolve()
        if base != target and base not in target.parents:
            return self.send_error(HTTPStatus.FORBIDDEN)
        if not target.is_file():
            return self.send_error(HTTPStatus.NOT_FOUND)
        size = target.stat().st_size
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        start, end = 0, size - 1
        status = HTTPStatus.OK
        match = re.match(r"bytes=(\d*)-(\d*)$", self.headers.get("Range", ""))
        if match and size:
            first, last = match.groups()
            if first:
                start = int(first)
                end = int(last) if last else size - 1
            elif last:  # derniers N octets
                start = max(0, size - int(last))
            end = min(end, size - 1)
            if start > end:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            status = HTTPStatus.PARTIAL_CONTENT
        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        if target.parent == PLAYER_DIR:
            self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            with open(target, "rb") as fh:
                fh.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = fh.read(min(1 << 20, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass  # le navigateur a coupé (changement de diapositive)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    CFG.inbox.mkdir(parents=True, exist_ok=True)
    CFG.cache.mkdir(parents=True, exist_ok=True)
    PLAYLIST.refresh()
    threading.Thread(target=playlist_refresh_loop, daemon=True).start()
    if CFG.sync_enabled:
        threading.Thread(target=sync_loop, daemon=True).start()
    else:
        log.info("synchronisation Drive désactivée (SYNC_ENABLED=0) : dossier local %s", CFG.inbox)
    httpd = ThreadingHTTPServer((CFG.host, CFG.port), Handler)
    httpd.daemon_threads = True
    log.info("vitrine démarrée sur http://%s:%d/", CFG.host, CFG.port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
