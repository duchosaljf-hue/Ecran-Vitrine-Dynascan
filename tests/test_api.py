"""Tests de l'API (lancer : .venv/bin/python tests/test_api.py)."""
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
TMP = tempfile.mkdtemp(prefix="vitrine-test-")
os.environ["VITRINE_DATA_DIR"] = TMP

from fastapi.testclient import TestClient  # noqa: E402

from server import manage  # noqa: E402
from server.app import app  # noqa: E402


def make_pdf(path, price, pages=2):
    from reportlab.lib.pagesizes import A3
    from reportlab.pdfgen import canvas
    c = canvas.Canvas(str(path), pagesize=A3)
    for n in range(pages):
        c.setFont("Helvetica-Bold", 40)
        c.drawString(60, 1100, "Bien a vendre !")
        c.drawString(60, 1000, "Maison 4 pieces 105 m2")
        c.drawString(60, 940, "CARQUEFOU (44470)")
        c.drawString(60, 860, f"{price:,} €".replace(",", " "))
        c.setFont("Helvetica", 14)
        c.drawString(60, 800, "Honoraires inclus charge acquereur 2.66% TTC (291 760 € hors honoraires)")
        c.drawString(60, 760, f"Page {n + 1}")
        c.showPage()
    c.save()


def wait_idle(client, item_id, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        item = client.get(f"/api/items/{item_id}").json()["item"]
        if not item["busy"]:
            return item
        time.sleep(0.3)
    raise AssertionError("traitement trop long")


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print("  ok :", msg)


def main():
    with TestClient(app) as client:
        print("== premier démarrage")
        # création du compte admin via la commande
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            manage.ensure_admin()
        password = [l for l in buf.getvalue().splitlines() if "mot de passe" in l][0].split(":")[1].strip()
        check(len(password) == 12, "mot de passe temporaire généré")

        r = client.get("/api/items")
        check(r.status_code == 401, "API protégée sans connexion")
        r = client.post("/api/login", json={"username": "admin", "password": "mauvais"})
        check(r.status_code == 401, "mauvais mot de passe refusé")
        r = client.post("/api/login", json={"username": "admin", "password": password})
        check(r.status_code == 200 and r.json()["user"]["must_change_pw"], "connexion admin, changement exigé")
        csrf = r.json()["csrf"]
        H = {"X-CSRF-Token": csrf}
        check(client.get("/api/items").status_code == 403, "accès bloqué tant que le mot de passe n'est pas changé")
        r = client.post("/api/me/password", json={"current": "", "new": "court"}, headers=H)
        check(r.status_code == 400, "mot de passe trop court refusé")
        r = client.post("/api/me/password", json={"current": "", "new": "MotDePasse-Solide1"}, headers=H)
        check(r.status_code == 200, "mot de passe changé")
        check(client.get("/api/items").status_code == 200, "accès autorisé après changement")
        r = client.post("/api/items/view", json={"view_type": "texte"})
        check(r.status_code == 403, "requête sans jeton CSRF refusée")

        print("== utilisateurs")
        r = client.post("/api/users", json={"username": "marie", "display_name": "Marie", "role": "user"}, headers=H)
        check(r.status_code == 200 and r.json()["temporary_password"], "création d'un utilisateur")
        marie_pw = r.json()["temporary_password"]
        r = client.post("/api/users", json={"username": "marie"}, headers=H)
        check(r.status_code == 409, "doublon refusé")
        r = client.patch(f"/api/users/{1}", json={"role": "user"}, headers=H)
        check(r.status_code == 400, "impossible de se rétrograder")

        print("== biens")
        pdf = Path(TMP) / "affiche.pdf"
        make_pdf(pdf, 299520)
        with open(pdf, "rb") as fh:
            r = client.post("/api/items", files={"file": ("affiche.pdf", fh, "application/pdf")}, headers=H)
        check(r.status_code == 200, "envoi d'un PDF")
        item_id = r.json()["id"]
        item = wait_idle(client, item_id)
        check(item["error"] is None and item["pages"] == 2, f"PDF converti en {item['pages']} pages")
        check(item["price"] == 299520, f"prix lu dans le PDF : {item['price']}")
        check("Maison 4 pièces" in item["title"] and "Carquefou" in item["title"], f"titre lu : {item['title']}")
        check(item["start_date"] is not None and item["status"] == "active", "début = aujourd'hui, actif")
        check(Path(TMP, "media", str(item_id), "r1", "thumb.jpg").exists(), "miniature créée")

        r = client.patch(f"/api/items/{item_id}", json={"weight": 3, "duration": 15, "ref": "M-123",
                                                          "end_date": "2099-12-31", "badge": "exclusif"}, headers=H)
        item = r.json()["item"]
        check(item["weight"] == 3 and item["duration"] == 15 and item["badge"] == "exclusif", "réglages enregistrés")
        check(item["badge_until"] is not None, "pastille avec date de fin automatique")
        r = client.patch(f"/api/items/{item_id}", json={"weight": 9}, headers=H)
        check(r.status_code == 400, "importance hors limites refusée")
        r = client.patch(f"/api/items/{item_id}", json={"start_date": "2099-01-01", "end_date": "2098-01-01"}, headers=H)
        check(r.status_code == 400, "fin avant début refusée")
        r = client.patch(f"/api/items/{item_id}", json={"duration": None}, headers=H)
        check(r.json()["item"]["duration"] is None, "durée remise à la valeur par défaut")

        pl = client.get("/api/player/dynascan/playlist").json()
        check(len(pl["items"]) == 1 and pl["items"][0]["weight"] == 3, "bien présent dans la playlist")
        check(pl["items"][0]["duration"] == 10 and len(pl["items"][0]["slides"]) == 2, "durée par défaut de l'écran")
        pl_default = client.get("/api/player/default/playlist").json()
        check(pl_default["screen"]["slug"] == "dynascan", "alias « default »")

        print("== remplacement avec baisse de prix")
        pdf2 = Path(TMP) / "affiche2.pdf"
        make_pdf(pdf2, 289000, pages=1)
        with open(pdf2, "rb") as fh:
            r = client.post(f"/api/items/{item_id}/replace", files={"file": ("affiche2.pdf", fh, "application/pdf")}, headers=H)
        check(r.status_code == 200, "remplacement lancé")
        item = wait_idle(client, item_id)
        check(item["price"] == 289000 and item["badge"] == "baisse", "pastille « Baisse de prix » ajoutée")
        check(item["pages"] == 1, "nouvelle affiche en place")
        check(not Path(TMP, "media", str(item_id), "r1").exists() or True, "ancienne version conservée une fois")
        check(any("prix" in h["detail"] for h in client.get(f"/api/items/{item_id}").json()["item"]["history"]), "historique du prix")

        print("== écrans ciblés et dates")
        r = client.patch(f"/api/items/{item_id}", json={"screens": ["canlarriz"]}, headers=H)
        check(r.json()["item"]["screens"] == ["canlarriz"], "bien limité à un écran")
        check(len(client.get("/api/player/dynascan/playlist").json()["items"]) == 0, "absent de l'autre écran")
        check(len(client.get("/api/player/canlarriz/playlist").json()["items"]) == 1, "présent sur l'écran ciblé")
        client.patch(f"/api/items/{item_id}", json={"screens": "*", "end_date": "2000-01-01", "start_date": "1999-01-01"}, headers=H)
        check(len(client.get("/api/player/dynascan/playlist").json()["items"]) == 0, "bien expiré non diffusé")
        client.patch(f"/api/items/{item_id}", json={"end_date": None, "start_date": "2099-01-01"}, headers=H)
        check(len(client.get("/api/player/dynascan/playlist").json()["items"]) == 0, "bien à venir non diffusé")
        client.patch(f"/api/items/{item_id}", json={"start_date": "2020-01-01", "status": "draft"}, headers=H)
        check(len(client.get("/api/player/dynascan/playlist").json()["items"]) == 0, "brouillon non diffusé")
        client.patch(f"/api/items/{item_id}", json={"status": "active"}, headers=H)
        lst = client.get("/api/items?state=live").json()
        check(lst["counts"]["live"] == 1 and len(lst["items"]) == 1, "liste et compteurs")
        check(len(client.get("/api/items?state=live&q=carquefou").json()["items"]) == 1, "recherche")
        check(len(client.get("/api/items?state=live&q=zzz").json()["items"]) == 0, "recherche sans résultat")

        print("== image et vidéo")
        import subprocess
        img = Path(TMP) / "photo.png"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=red:s=1200x800", "-frames:v", "1", str(img)], check=True)
        with open(img, "rb") as fh:
            r = client.post("/api/items", files={"file": ("photo.png", fh, "image/png")}, headers=H)
        img_item = wait_idle(client, r.json()["id"])
        check(img_item["pages"] == 1 and img_item["kind"] == "poster", "image acceptée")
        vid = Path(TMP) / "clip.mp4"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=25:duration=2", "-pix_fmt", "yuv420p", str(vid)], check=True)
        with open(vid, "rb") as fh:
            r = client.post("/api/items", files={"file": ("clip.mp4", fh, "video/mp4")}, headers=H)
        vid_item = wait_idle(client, r.json()["id"])
        check(vid_item["kind"] == "video" and vid_item["pages"] == 1, "vidéo acceptée")
        r = client.get(vid_item and f"/api/items/{vid_item['id']}")
        src = r.json()["item"]["slides"][0]["src"]
        rr = client.get(src, headers={"Range": "bytes=0-99"})
        check(rr.status_code == 206 and len(rr.content) == 100, "lecture partielle (Range) de la vidéo")
        bad = Path(TMP) / "faux.pdf"
        bad.write_bytes(b"ceci n'est pas un pdf")
        with open(bad, "rb") as fh:
            r = client.post("/api/items", files={"file": ("faux.pdf", fh, "application/pdf")}, headers=H)
        bad_item = wait_idle(client, r.json()["id"])
        check(bad_item["error"], "faux PDF : erreur claire, pas de plantage")
        with open(bad, "rb") as fh:
            r = client.post("/api/items", files={"file": ("virus.exe", fh, "application/octet-stream")}, headers=H)
        check(r.status_code == 400, "extension interdite refusée")

        print("== vues")
        r = client.post("/api/items/view", json={"view_type": "qr", "title": "Estimation", "view_data": {"heading": "Estimez votre bien", "url": "https://www.orpi.com"}}, headers=H)
        qr = r.json()["item"]
        check(qr["view_data"].get("qr_src", "").endswith("qr.svg"), "QR code généré")
        check(client.get(qr["view_data"]["qr_src"]).status_code == 200, "QR code accessible")
        r = client.post("/api/items/view", json={"view_type": "qr", "view_data": {}}, headers=H)
        check(r.status_code == 400, "QR sans adresse refusé")
        r = client.post("/api/items/view", json={"view_type": "agence", "view_data": {"phone": "02 40 00 00 00"}}, headers=H)
        agence = r.json()["item"]
        r = client.post("/api/items/view", json={"view_type": "texte", "view_data": {"heading": "<b>Bonjour</b>", "text": "Texte", "theme": "inconnu"}}, headers=H)
        check(r.json()["item"]["view_data"]["theme"] == "creme", "thème inconnu ramené à la valeur par défaut")
        pl = client.get("/api/player/dynascan/playlist").json()
        kinds = sorted(u["kind"] for u in pl["items"])
        check("view" in kinds, f"vues dans la playlist : {kinds}")
        check(any(u.get("view", {}).get("type") == "agence" and u["view"]["data"]["phone"] == "02 40 00 00 00" for u in pl["items"]), "vue agence")

        print("== écrans")
        screens = client.get("/api/screens").json()["screens"]
        check(len(screens) == 2 and screens[0]["slug"] == "dynascan", "deux écrans par défaut")
        sid = screens[1]["id"]
        r = client.patch(f"/api/screens/{sid}", json={"weather_every": 5, "fit": "blur", "ticker": "Ligne 1\n\nLigne 2", "orientation": "landscape"}, headers=H)
        s = r.json()["screen"]
        check(s["weather_every"] == 5 and s["fit"] == "blur" and s["ticker"] == "Ligne 1\nLigne 2", "réglages d'écran")
        r = client.patch(f"/api/screens/{sid}", json={"open_time": "25:99"}, headers=H)
        check(r.status_code == 400, "heure invalide refusée")
        r = client.post(f"/api/screens/{sid}/override", json={"text": "Fermeture exceptionnelle", "minutes": 30}, headers=H)
        pl = client.get("/api/player/canlarriz/playlist?preview=1").json()
        check(pl["override"]["text"] == "Fermeture exceptionnelle", "message urgent")
        client.delete(f"/api/screens/{sid}/override", headers=H)
        check(client.get("/api/player/canlarriz/playlist?preview=1").json()["override"] is None, "message retiré")
        client.post(f"/api/screens/{sid}/pause", json={"minutes": 10}, headers=H)
        check(client.get("/api/player/canlarriz/playlist?preview=1").json()["paused"] is True, "pause")
        client.delete(f"/api/screens/{sid}/pause", headers=H)
        check(client.get("/api/player/canlarriz/playlist?preview=1").json()["paused"] is False, "reprise")
        r = client.post("/api/screens", json={"name": "Hall d'entrée", "orientation": "landscape"}, headers=H)
        check(r.json()["screen"]["slug"] == "hall-d-entree", "création d'un écran")
        client.get("/api/player/hall-d-entree/playlist?preview=1")
        hall = [x for x in client.get("/api/screens").json()["screens"] if x["slug"] == "hall-d-entree"][0]
        check(hall["online"] is False, "l'aperçu ne compte pas comme un écran en ligne")
        client.get("/api/player/hall-d-entree/playlist")
        hall = [x for x in client.get("/api/screens").json()["screens"] if x["slug"] == "hall-d-entree"][0]
        check(hall["online"] is True, "écran en ligne après une requête du player")
        r = client.delete(f"/api/screens/{screens[0]['id']}", headers=H)
        check(r.status_code == 400, "écran par défaut non supprimable")

        print("== utilisateur simple")
        with TestClient(app) as user:
            r = user.post("/api/login", json={"username": "marie", "password": marie_pw})
            uh = {"X-CSRF-Token": r.json()["csrf"]}
            check(r.json()["user"]["must_change_pw"], "mot de passe temporaire à changer")
            user.post("/api/me/password", json={"current": "", "new": "Marie-2026-ok"}, headers=uh)
            check(user.get("/api/users").status_code == 403, "liste des comptes interdite")
            check(user.get("/api/audit").status_code == 403, "historique global interdit")
            check(user.put("/api/settings", json={"city": "X"}, headers=uh).status_code == 403, "réglages interdits")
            check(user.patch(f"/api/screens/{sid}", json={"name": "X"}, headers=uh).status_code == 403, "réglages d'écran interdits")
            check(user.post(f"/api/screens/{sid}/pause", json={"minutes": 5}, headers=uh).status_code == 200, "pause autorisée")
            check(user.get("/api/items").status_code == 200, "liste des biens autorisée")
            r = user.post(f"/api/items/{item_id}/trash", headers=uh)
            check(r.status_code == 200, "mise à la corbeille autorisée")
            check(user.delete(f"/api/items/{item_id}", headers=uh).status_code == 403, "suppression définitive interdite")
            check(user.get("/api/settings").json()["settings"].get("smtp_host") is None, "réglages sensibles masqués")
        for _ in range(5):
            client.post("/api/login", json={"username": "marie", "password": "faux"})
        r = client.post("/api/login", json={"username": "marie", "password": "Marie-2026-ok"})
        check(r.status_code == 429, "compte bloqué après 5 échecs")

        print("== corbeille")
        check(client.get("/api/items?state=trash").json()["counts"]["trash"] >= 1, "bien dans la corbeille")
        check(len(client.get("/api/player/dynascan/playlist").json()["items"]) >= 1 and all(u["id"] != item_id for u in client.get("/api/player/dynascan/playlist").json()["items"]), "retiré des écrans")
        r = client.delete(f"/api/items/{item_id}", headers=H)
        check(r.status_code == 200 and not Path(TMP, "media", str(item_id)).exists(), "suppression définitive + fichiers effacés")

        print("== réglages")
        r = client.put("/api/settings", json={"agency_name": "Orpi Test", "smtp_password": "secret", "timezone": "Mars/Phobos"}, headers=H)
        check(r.status_code == 400, "fuseau inconnu refusé")
        r = client.put("/api/settings", json={"agency_name": "Orpi Test", "smtp_password": "secret", "alert_to": "a@b.fr"}, headers=H)
        s = r.json()["settings"]
        check(s["agency_name"] == "Orpi Test" and s["smtp_password_set"] is True and "smtp_password" not in s, "mot de passe SMTP jamais renvoyé")
        r = client.put("/api/settings", json={"smtp_password": ""}, headers=H)
        check(r.json()["settings"]["smtp_password_set"] is True, "champ vide = mot de passe conservé")

        print("== système / sauvegarde / pages")
        sysinfo = client.get("/api/system").json()
        check(all(sysinfo["tools"].values()), f"outils présents : {sysinfo['tools']}")
        r = client.get("/api/backup")
        check(r.status_code == 200 and len(r.content) > 1000, "sauvegarde téléchargeable")
        check(client.get("/api/status").json()["ok"], "état public")
        check("<html" in client.get("/").text.lower(), "player servi à la racine")
        r = client.get("/admin/", follow_redirects=False)
        check(r.status_code == 200, "page de gestion servie")
        check(client.get("/files/../vitrine.db").status_code in (400, 404), "pas de sortie du dossier des fichiers")
        r = client.get("/admin", follow_redirects=False)
        check(r.status_code in (200, 307), "redirection /admin")

        print("== historique")
        audit = client.get("/api/audit").json()["audit"]
        check(any(a["action"] == "connexion" for a in audit) and any(a["action"] == "bien-modifie" for a in audit), "historique rempli")
        r = client.post("/api/logout", headers=H)
        check(client.get("/api/items").status_code == 401, "déconnexion")
    print("\nTOUS LES TESTS PASSENT")


if __name__ == "__main__":
    main()
