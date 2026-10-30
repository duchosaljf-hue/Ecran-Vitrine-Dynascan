"""Tests complémentaires : reprise de l'ancien dossier, alertes e-mail (lancer : .venv/bin/python tests/test_extra.py)."""
import os
import socketserver
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
TMP = tempfile.mkdtemp(prefix="vitrine-extra-")
os.environ["VITRINE_DATA_DIR"] = TMP

from server import importer, monitor  # noqa: E402
from server.db import db, get_settings, init_db, iso, set_settings, utcnow  # noqa: E402
from datetime import timedelta  # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print("  ok :", msg)


class FakeSMTP(socketserver.StreamRequestHandler):
    mails = []

    def handle(self):
        w = lambda t: self.wfile.write((t + "\r\n").encode())
        w("220 fake ESMTP")
        data_mode, body = False, []
        for raw in self.rfile:
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if data_mode:
                if line == ".":
                    FakeSMTP.mails.append("\n".join(body))
                    data_mode = False
                    w("250 OK queued")
                else:
                    body.append(line)
                continue
            cmd = line.upper()
            if cmd.startswith("EHLO") or cmd.startswith("HELO"):
                w("250 fake")
            elif cmd.startswith("MAIL") or cmd.startswith("RCPT"):
                w("250 OK")
            elif cmd == "DATA":
                data_mode = True
                w("354 go")
            elif cmd == "QUIT":
                w("221 bye")
                return
            else:
                w("250 OK")


def main():
    print("== reprise de l'ancien dossier")
    inbox = Path(TMP) / "inbox"
    (inbox / "sous").mkdir(parents=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=blue:s=800x600", "-frames:v", "1",
                    str(inbox / "02-offre [duree=20] [fin=2099-01-01].png")], check=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=green:s=800x600", "-frames:v", "1",
                    str(inbox / "sous" / "01-autre.png")], check=True)
    (inbox / "messages.txt").write_text("# commentaire\nBienvenue\nNouveau : estimation gratuite\n", encoding="utf-8")
    (inbox / "notes.docx").write_text("pas un média")
    init_db()
    importer.import_inbox_once()
    with db() as conn:
        items = conn.execute("SELECT * FROM items ORDER BY id").fetchall()
        ticker = conn.execute("SELECT ticker FROM screens WHERE slug = 'dynascan'").fetchone()[0]
        flag = get_settings(conn)["inbox_imported"]
    check(len(items) == 2, f"2 fichiers repris (le .docx est ignoré) : {[i['title'] for i in items]}")
    offer = [i for i in items if i["duration"] == 20][0]
    check(offer["end_date"] == "2099-01-01" and offer["title"] == "offre", "options [duree=] et [fin=] reprises, préfixe d'ordre retiré")
    check(ticker == "Bienvenue\nNouveau : estimation gratuite", "messages.txt repris dans le bandeau de l'écran par défaut")
    check(flag == "1", "reprise faite une seule fois")
    importer.import_inbox_once()
    with db() as conn:
        check(conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 2, "pas de doublon au redémarrage suivant")

    print("== alertes e-mail")
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), FakeSMTP)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    with db() as conn:
        set_settings(conn, {"smtp_host": "127.0.0.1", "smtp_port": port, "smtp_security": "none", "alert_to": "moi@exemple.fr, toi@exemple.fr",
                            "smtp_from": "vitrine@exemple.fr", "alerts_enabled": "1"})
    try:
        monitor.send_mail("Test", "Bonjour")
        ok = True
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(exc)
    check(ok and any("Bonjour" in m and "Subject: [Vitrine] Test" in m for m in FakeSMTP.mails), "e-mail de test reçu")

    old = iso(utcnow() - timedelta(minutes=45))
    with db() as conn:
        conn.execute("UPDATE screens SET last_seen = ?, open_time = '00:00', close_time = '23:59', alerted = 0 WHERE slug = 'dynascan'", (old,))
        conn.execute("UPDATE screens SET last_seen = ?, alerted = 0 WHERE slug = 'canlarriz'", (iso(utcnow()),))
    FakeSMTP.mails.clear()
    monitor.check_screens()
    check(len(FakeSMTP.mails) == 1 and "Écran hors ligne" in FakeSMTP.mails[0].replace("=C3=89", "É") or any("hors ligne" in m for m in FakeSMTP.mails), "alerte « écran hors ligne » envoyée")
    monitor.check_screens()
    check(len(FakeSMTP.mails) == 1, "une seule alerte par panne")
    with db() as conn:
        conn.execute("UPDATE screens SET alerted = 0, open_time = '03:00', close_time = '03:01' WHERE slug = 'dynascan'")
    FakeSMTP.mails.clear()
    monitor.check_screens()
    check(len(FakeSMTP.mails) == 0 or datetime_in_window(), "pas d'alerte en dehors des heures d'ouverture")
    with db() as conn:
        set_settings(conn, {"alerts_enabled": "0"})
        conn.execute("UPDATE screens SET alerted = 0, open_time = '00:00', close_time = '23:59' WHERE slug = 'dynascan'")
    FakeSMTP.mails.clear()
    monitor.check_screens()
    check(len(FakeSMTP.mails) == 0, "alertes désactivées : aucun e-mail")

    print("== sauvegardes et corbeille")
    path = monitor.make_backup("test")
    check(path.exists() and path.stat().st_size > 1000, "sauvegarde de la base créée")
    with db() as conn:
        conn.execute("UPDATE items SET status = 'trash', trashed_at = ? WHERE id = ?", (iso(utcnow() - timedelta(days=40)), items[0]["id"]))
    monitor.purge_trash()
    with db() as conn:
        check(conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1, "corbeille purgée après 30 jours")
    print("\nTESTS COMPLÉMENTAIRES OK")


def datetime_in_window():
    from datetime import datetime
    return datetime.now().hour == 3 and datetime.now().minute <= 1


if __name__ == "__main__":
    main()
