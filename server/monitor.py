"""Tâches de fond : température du NUC, état des écrans, alertes e-mail, sauvegardes, nettoyage."""
import logging
import shutil
import smtplib
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from email.message import EmailMessage
from pathlib import Path

from .config import CFG
from .db import connect, db, get_settings, iso, local_tz, now_iso, parse_iso, utcnow

log = logging.getLogger("vitrine.monitor")

OFFLINE_ALERT_MINUTES = 10
TRASH_DAYS = 30
BACKUPS_KEPT = 14


# ------------------------------------------------------------------ température
def read_cpu_temp():
    """Température la plus haute relevée par le noyau, en °C (None si indisponible)."""
    values = []
    for pattern in ("thermal/thermal_zone*/temp", ):
        for zone in Path("/sys/class").glob(pattern):
            try:
                values.append(int(zone.read_text().strip()) / 1000.0)
            except (OSError, ValueError):
                continue
    for sensor in Path("/sys/class/hwmon").glob("hwmon*/temp*_input"):
        try:
            values.append(int(sensor.read_text().strip()) / 1000.0)
        except (OSError, ValueError):
            continue
    values = [v for v in values if 0 < v < 150]
    return round(max(values), 1) if values else None


class TempState:
    def __init__(self):
        self.lock = threading.Lock()
        self.current = None
        self._window = []

    def record(self, value):
        now = time.time()
        with self.lock:
            self.current = value
            if value is not None:
                self._window.append((now, value))
            self._window = [(t, v) for t, v in self._window if now - t < 86400]

    def snapshot(self):
        with self.lock:
            peak = max((v for _, v in self._window), default=None)
            return {"current_c": self.current, "max_24h_c": peak, "warn_c": CFG.temp_warn_c}


TEMP = TempState()


# ------------------------------------------------------------------ e-mail
def send_mail(subject, body):
    """Envoie un e-mail d'alerte avec les réglages SMTP. Retourne le destinataire ; lève une exception en cas d'échec."""
    settings = get_settings()
    recipients = [r.strip() for r in settings["alert_to"].replace(";", ",").split(",") if r.strip()]
    host = settings["smtp_host"]
    if not recipients or not host:
        raise RuntimeError("Renseignez le serveur SMTP et le destinataire dans les Réglages.")
    sender = settings["smtp_from"] or settings["smtp_user"] or f"vitrine@{host}"
    msg = EmailMessage()
    msg["Subject"] = f"[Vitrine] {subject}"
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg.set_content(body)
    port = int(settings["smtp_port"] or 587)
    security = settings["smtp_security"]
    if security == "ssl":
        client = smtplib.SMTP_SSL(host, port, timeout=20)
    else:
        client = smtplib.SMTP(host, port, timeout=20)
    try:
        if security == "starttls":
            client.starttls()
        if settings["smtp_user"]:
            client.login(settings["smtp_user"], settings["smtp_password"])
        client.send_message(msg)
    finally:
        try:
            client.quit()
        except Exception:  # noqa: BLE001
            pass
    return ", ".join(recipients)


def _alert(subject, body):
    settings = get_settings()
    if settings["alerts_enabled"] != "1":
        return
    try:
        send_mail(subject, body)
        log.info("alerte envoyée : %s", subject)
    except Exception as exc:  # noqa: BLE001
        log.warning("alerte non envoyée (%s) : %s", subject, exc)


# ------------------------------------------------------------------ écrans hors ligne
def within_hours(screen, now_local):
    try:
        open_h, open_m = map(int, screen["open_time"].split(":"))
        close_h, close_m = map(int, screen["close_time"].split(":"))
    except ValueError:
        return True
    minutes = now_local.hour * 60 + now_local.minute
    start, end = open_h * 60 + open_m, close_h * 60 + close_m
    if start <= end:
        return start + 5 <= minutes <= end
    return minutes >= start + 5 or minutes <= end  # plage qui passe minuit


def check_screens():
    now = utcnow()
    with db() as conn:
        settings = get_settings(conn)
        tz = local_tz(settings)
        rows = conn.execute("SELECT * FROM screens").fetchall()
        to_alert = []
        for screen in rows:
            seen = parse_iso(screen["last_seen"])
            if not seen:
                continue
            offline = (now - seen) > timedelta(minutes=OFFLINE_ALERT_MINUTES)
            if offline and within_hours(screen, now.astimezone(tz)) and not screen["alerted"]:
                conn.execute("UPDATE screens SET alerted = 1 WHERE id = ?", (screen["id"],))
                to_alert.append((screen["name"], screen["last_seen"]))
    for name, last_seen in to_alert:
        _alert(f"Écran hors ligne : {name}",
               f"L'écran « {name} » ne répond plus depuis plus de {OFFLINE_ALERT_MINUTES} minutes "
               f"(dernier signe de vie : {last_seen}).\n\nVérifiez qu'il est allumé et connecté au Wi-Fi.")


_last_temp_alert = 0.0


def check_temperature():
    global _last_temp_alert
    value = read_cpu_temp()
    TEMP.record(value)
    if value is not None and value >= CFG.temp_warn_c:
        log.warning("température élevée : %.1f °C (seuil %.0f °C)", value, CFG.temp_warn_c)
        if time.time() - _last_temp_alert > 3600:
            _last_temp_alert = time.time()
            _alert("Température élevée du NUC",
                   f"Le NUC atteint {value:.0f} °C (seuil d'alerte {CFG.temp_warn_c:.0f} °C). "
                   "Pensez à le dépoussiérer et à dégager ses aérations.")


# ------------------------------------------------------------------ sauvegardes et nettoyage
def make_backup(tag="auto"):
    CFG.backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = CFG.backup_dir / f"vitrine-{stamp}-{tag}.db"
    src = connect()
    try:
        out = sqlite3.connect(str(dest))
        try:
            src.backup(out)
        finally:
            out.close()
    finally:
        src.close()
    return dest


def prune_backups():
    backups = sorted(CFG.backup_dir.glob("vitrine-*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in backups[BACKUPS_KEPT:]:
        old.unlink(missing_ok=True)


def purge_trash():
    from . import media
    cutoff = iso(utcnow() - timedelta(days=TRASH_DAYS))
    with db() as conn:
        rows = conn.execute("SELECT id FROM items WHERE status = 'trash' AND trashed_at < ?", (cutoff,)).fetchall()
        for row in rows:
            conn.execute("DELETE FROM items WHERE id = ?", (row["id"],))
    for row in rows:
        media.delete_item_files(row["id"])
    if rows:
        log.info("%d bien(s) supprimé(s) de la corbeille (plus de %d jours)", len(rows), TRASH_DAYS)


def clean_tmp():
    cutoff = time.time() - 6 * 3600
    for path in CFG.tmp_dir.glob("*"):
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            pass


_last_daily = ""


def daily_jobs():
    global _last_daily
    today = datetime.now().strftime("%Y-%m-%d")
    if _last_daily == today or datetime.now().hour < 3:
        return
    _last_daily = today
    make_backup("auto")
    prune_backups()
    purge_trash()
    clean_tmp()


def loop():
    while True:
        for job in (check_temperature, check_screens, daily_jobs):
            try:
                job()
            except Exception:  # noqa: BLE001
                log.exception("tâche de fond en échec : %s", job.__name__)
        time.sleep(60)


_started = False


def start():
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=loop, daemon=True, name="monitor").start()
