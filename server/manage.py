"""Commandes d'administration en ligne de commande.

Utilisation (depuis le dossier du projet) :
    .venv/bin/python -m server.manage ensure-admin       crée le compte « admin » s'il n'existe pas encore
    .venv/bin/python -m server.manage reset-password NOM  nouveau mot de passe temporaire pour un compte
    .venv/bin/python -m server.manage backup              sauvegarde de la base de données
"""
import sys

from . import auth, monitor
from .config import CFG
from .db import audit, db, init_db, now_iso


def ensure_admin():
    init_db()
    with db() as conn:
        count = conn.execute("SELECT COUNT(*) FROM users WHERE role = 'admin'").fetchone()[0]
        if count:
            print("Un compte administrateur existe déjà : rien à faire.")
            return 0
        password = auth.random_password()
        conn.execute(
            "INSERT INTO users(username, display_name, pw_hash, role, must_change_pw, created_at)"
            " VALUES ('admin', 'Administrateur', ?, 'admin', 1, ?)", (auth.hash_password(password), now_iso()))
        audit(conn, "système", "utilisateur-cree", None, "admin (premier démarrage)")
    print("=" * 60)
    print("Compte administrateur créé :")
    print("   identifiant   : admin")
    print(f"   mot de passe  : {password}")
    print("Notez-le : il ne sera plus affiché. Il faudra le changer à la première connexion.")
    print("=" * 60)
    return 0


def reset_password(username):
    init_db()
    password = auth.random_password()
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if not row:
            print(f"Compte introuvable : {username}")
            return 1
        conn.execute("UPDATE users SET pw_hash = ?, must_change_pw = 1, active = 1, failed_logins = 0,"
                     " locked_until = NULL WHERE id = ?", (auth.hash_password(password), row["id"]))
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (row["id"],))
        audit(conn, "système", "mot-de-passe-reinitialise", None, username)
    print(f"Nouveau mot de passe temporaire pour « {username} » : {password}")
    print("Il devra être changé à la prochaine connexion.")
    return 0


def backup():
    init_db()
    path = monitor.make_backup("manuel")
    print(f"Sauvegarde créée : {path}")
    return 0


def main(argv):
    if len(argv) >= 2 and argv[1] == "ensure-admin":
        return ensure_admin()
    if len(argv) >= 3 and argv[1] == "reset-password":
        return reset_password(argv[2])
    if len(argv) >= 2 and argv[1] == "backup":
        return backup()
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
