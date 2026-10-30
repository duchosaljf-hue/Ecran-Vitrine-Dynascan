"""Base de données SQLite : schéma, accès, réglages."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone

from .config import CFG

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    display_name TEXT NOT NULL DEFAULT '',
    pw_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('admin', 'user')),
    active INTEGER NOT NULL DEFAULT 1,
    must_change_pw INTEGER NOT NULL DEFAULT 0,
    failed_logins INTEGER NOT NULL DEFAULT 0,
    locked_until TEXT,
    created_at TEXT NOT NULL,
    last_login TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    csrf TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS screens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    orientation TEXT NOT NULL DEFAULT 'portrait',
    order_mode TEXT NOT NULL DEFAULT 'random',
    default_duration INTEGER NOT NULL DEFAULT 10,
    weather_every INTEGER NOT NULL DEFAULT 3,
    weather_seconds INTEGER NOT NULL DEFAULT 10,
    corner_clock INTEGER NOT NULL DEFAULT 0,
    fit TEXT NOT NULL DEFAULT 'contain',
    ticker TEXT NOT NULL DEFAULT '',
    open_time TEXT NOT NULL DEFAULT '07:00',
    close_time TEXT NOT NULL DEFAULT '21:30',
    hours_enabled INTEGER NOT NULL DEFAULT 0,
    override_text TEXT,
    override_until TEXT,
    paused_until TEXT,
    last_seen TEXT,
    last_ip TEXT,
    alerted INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL DEFAULT 'poster' CHECK (kind IN ('poster', 'video', 'view')),
    title TEXT NOT NULL DEFAULT '',
    ref TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'draft', 'trash')),
    error TEXT,
    duration INTEGER,
    weight INTEGER NOT NULL DEFAULT 1,
    priority_locked INTEGER NOT NULL DEFAULT 0,
    start_date TEXT,
    end_date TEXT,
    badge TEXT NOT NULL DEFAULT '',
    badge_until TEXT,
    screens TEXT NOT NULL DEFAULT '*',
    price INTEGER,
    rev INTEGER NOT NULL DEFAULT 0,
    slides TEXT NOT NULL DEFAULT '[]',
    thumb TEXT,
    view_type TEXT,
    view_data TEXT NOT NULL DEFAULT '{}',
    created_by INTEGER,
    created_at TEXT NOT NULL,
    updated_by INTEGER,
    updated_at TEXT NOT NULL,
    trashed_at TEXT
);

CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    username TEXT NOT NULL,
    action TEXT NOT NULL,
    item_id INTEGER,
    detail TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS audit_item ON audit(item_id);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def default_settings():
    return {
        "agency_name": "Orpi Carquefou",
        "agency_address": "",
        "agency_phone": "",
        "agency_hours": "",
        "agency_site": "www.orpi.com",
        "city": CFG.city,
        "latitude": CFG.latitude,
        "longitude": CFG.longitude,
        "timezone": CFG.timezone,
        "default_duration": str(CFG.default_duration),
        "default_weight": "3",
        "priority_levels": "0",
        # réglage administrateur : passages par tour et durée (en % de la durée de l'écran) de chaque couleur
        "priority_weight_high": "5",
        "priority_weight_medium": "3",
        "priority_weight_low": "1",
        "priority_pct_high": "150",
        "priority_pct_medium": "100",
        "priority_pct_low": "70",
        "new_badge_days": "14",
        "default_screen": "dynascan",
        "alerts_enabled": "0",
        "alert_to": "",
        "smtp_host": "",
        "smtp_port": "587",
        "smtp_security": "starttls",
        "smtp_user": "",
        "smtp_password": "",
        "smtp_from": "",
        "inbox_imported": "0",
    }


def utcnow():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_iso():
    return iso(utcnow())


def parse_iso(text):
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def connect():
    conn = sqlite3.connect(str(CFG.db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


@contextmanager
def db():
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    CFG.ensure_dirs()
    with db() as conn:
        conn.executescript(SCHEMA)
        # bases créées avant l'option « écran noir en dehors des heures d'ouverture »
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(screens)")}
        if "hours_enabled" not in columns:
            conn.execute("ALTER TABLE screens ADD COLUMN hours_enabled INTEGER NOT NULL DEFAULT 0")
        item_columns = {r["name"] for r in conn.execute("PRAGMA table_info(items)")}
        if "priority_locked" not in item_columns:
            conn.execute("ALTER TABLE items ADD COLUMN priority_locked INTEGER NOT NULL DEFAULT 0")
        for key, value in default_settings().items():
            conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (key, value))
        # passage de l'importance 1-5 aux trois niveaux vert (5) / orange (3) / rouge (1), une seule fois :
        # l'ancienne « normale » (1-2) devient orange, l'ancienne « fréquente » (3 et plus) devient verte
        if conn.execute("SELECT value FROM settings WHERE key = 'priority_levels'").fetchone()[0] != "1":
            conn.execute("UPDATE items SET weight = CASE WHEN weight >= 3 THEN 5 ELSE 3 END")
            conn.execute("UPDATE settings SET value = '3' WHERE key = 'default_weight' AND value IN ('1', '2')")
            conn.execute("UPDATE settings SET value = '1' WHERE key = 'priority_levels'")
        if conn.execute("SELECT COUNT(*) FROM screens").fetchone()[0] == 0:
            now = now_iso()
            conn.execute(
                "INSERT INTO screens(slug, name, orientation, default_duration, weather_every, weather_seconds,"
                " corner_clock, created_at) VALUES (?,?,?,?,?,?,?,?)",
                ("dynascan", "Vitrine DynaScan", "portrait", CFG.default_duration, CFG.weather_every,
                 CFG.weather_seconds, int(CFG.corner_clock), now))
            conn.execute(
                "INSERT INTO screens(slug, name, orientation, default_duration, weather_every, weather_seconds,"
                " corner_clock, created_at) VALUES (?,?,?,?,?,?,?,?)",
                ("canlarriz", "Écran Canlarriz", "portrait", CFG.default_duration, CFG.weather_every,
                 CFG.weather_seconds, int(CFG.corner_clock), now))
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


# ------------------------------------------------------------------ réglages
def get_settings(conn=None):
    own = conn is None
    conn = conn or connect()
    try:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    finally:
        if own:
            conn.close()
    data = default_settings()
    data.update({r["key"]: r["value"] for r in rows})
    return data


def set_settings(conn, values):
    for key, value in values.items():
        conn.execute(
            "INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)))


def local_tz(settings=None):
    from zoneinfo import ZoneInfo
    settings = settings or get_settings()
    try:
        return ZoneInfo(settings.get("timezone") or "Europe/Paris")
    except Exception:  # noqa: BLE001
        return timezone.utc


def today_local(settings=None):
    return datetime.now(local_tz(settings)).date()


def add_days(day, days):
    return (day + timedelta(days=days)).isoformat()


# ------------------------------------------------------------------ audit
def audit(conn, username, action, item_id=None, detail=""):
    conn.execute(
        "INSERT INTO audit(ts, username, action, item_id, detail) VALUES (?,?,?,?,?)",
        (now_iso(), username, action, item_id, detail[:2000]))


def jloads(text, default):
    try:
        return json.loads(text) if text else default
    except (TypeError, ValueError):
        return default
