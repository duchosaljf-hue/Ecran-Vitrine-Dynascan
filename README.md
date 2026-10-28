# Vitrine : gestion et diffusion des écrans d'agence

Application qui tourne sur le **NUC** de l'agence. Elle permet à l'équipe d'ajouter des affiches (PDF, images, vidéos),
de régler leur diffusion, et de piloter **plusieurs écrans** (DynaScan, Canlarriz…) depuis un ordinateur, un téléphone ou un iPad.

```
 PC / téléphone / iPad ──►  http://ADRESSE-DU-NUC:8080/admin/      (page de gestion, avec mot de passe)
                                        │
                              NUC : serveur + base de données + fichiers
                                        │
        ┌───────────────────────────────┼────────────────────────────┐
        ▼                               ▼                            ▼
 écran du NUC (HDMI)           écran Canlarriz (Wi-Fi)          autres écrans
 http://127.0.0.1:8080/        http://ADRESSE:8080/?screen=canlarriz   …
```

## Ce que fait l'application

- **Biens** : on dépose un PDF d'affiche (ou une image, une vidéo). Le titre et le prix sont lus dans le PDF quand il contient du texte.
  Pour chaque bien : durée par page, **importance de 1 à 5** (plus c'est haut, plus le bien revient souvent), **date de début**
  (par défaut le jour de l'ajout) et **date de fin**, pastille (« Nouveau », « Baisse de prix », « Exclusivité », « Sous compromis », « Vendu »),
  écrans concernés, notes.
- **Baisse de prix** : quand on **remplace l'affiche** d'un bien par une version au prix plus bas, la pastille « Baisse de prix »
  est ajoutée automatiquement (14 jours par défaut), et le changement est noté dans l'historique.
- **Vues** : écrans d'information intercalés avec les affiches : *Annonce* (titre + texte), *QR code* (lien vers votre site, une estimation, une offre d'emploi…),
  *Agence* (nom, adresse, téléphone, horaires, repris des Réglages). Elles ont les mêmes réglages que les biens (durée, importance, dates, écrans).
- **Météo** : une vue plein écran (heure, météo actuelle, prévisions sur 4 jours) toutes les N diapositives, réglable **par écran**.
- **Ordre de diffusion** : aléatoire pondéré par l'importance, sans répétition immédiate (ou du plus récent au plus ancien).
- **Écrans** : un profil par écran (orientation, durée par défaut, ajustement des affiches, météo, bandeau de messages défilants), avec état en ligne / hors ligne,
  **aperçu**, **pause** et **message urgent** (affiché en grand sur fond rouge pendant la durée choisie).
- **Comptes** : un administrateur et des utilisateurs. Les utilisateurs ajoutent et modifient les affiches ; seul l'administrateur supprime
  définitivement, gère les comptes, les écrans et les réglages. Historique « qui a fait quoi ». Corbeille conservée 30 jours.
- **Alertes e-mail** (facultatif) : écran hors ligne pendant ses heures d'ouverture, NUC trop chaud.
- **Sauvegarde** automatique de la base chaque nuit (14 conservées), téléchargeable depuis les Réglages.

## Première connexion

Le script d'installation affiche **une seule fois** le mot de passe temporaire du compte `admin`. Connectez-vous sur
`http://ADRESSE-DU-NUC:8080/admin/`, choisissez votre mot de passe, puis créez les comptes de l'équipe (page *Utilisateurs*).

Mot de passe administrateur perdu : sur le NUC, `cd ~/Ecran-Vitrine-Dynascan && .venv/bin/python -m server.manage reset-password admin`.

## Brancher les écrans

Chaque écran a une **adresse** (page *Écrans*, bouton *Copier*) :

| Écran | Adresse |
|---|---|
| Écran branché au NUC (DynaScan) | `http://127.0.0.1:8080/` (lancée automatiquement au démarrage du NUC : écran par défaut) |
| Canlarriz et autres | `http://ADRESSE-DU-NUC:8080/?screen=canlarriz` |

Sur le Canlarriz : dans LedArt, élément **HTML**, « Chemin de la page » = l'adresse ci-dessus, **rafraîchissement désactivé**, durée de pause la plus grande possible.
Réservez à une **adresse IP fixe** le NUC dans la box (réservation DHCP) pour que les adresses ne changent pas.

L'heure et la date affichées viennent du NUC, pas de l'écran : un écran mal réglé affiche quand même la bonne heure.

## Installation sur le NUC (Ubuntu Desktop 24.04)

```bash
sudo apt install -y git
git clone -b claude/vitrine-player https://github.com/duchosaljf-hue/Ecran-Vitrine-Dynascan.git
cd Ecran-Vitrine-Dynascan
sudo ./scripts/install.sh
sudo reboot        # quand le NUC est branché sur l'écran portrait
```

Si l'image de l'écran du NUC est couchée au premier démarrage, changez `SCREEN_ROTATION` (`left` ↔ `right`) dans `config.env`, puis redémarrez.

## Mise à jour

```bash
cd ~/Ecran-Vitrine-Dynascan
git pull
sudo ./scripts/install.sh --update
```

(ou simplement `./scripts/update.sh`). Les affiches, comptes et réglages sont conservés (dossier `data/`).
La première mise à jour depuis l'ancienne version reprend automatiquement les fichiers de l'ancien dossier synchronisé (`data/inbox`) comme biens.
La synchronisation Google Drive n'existe plus : on dépose désormais les affiches dans la page de gestion.

## Réglages de l'écran DynaScan (télécommande, touche MENU)

| Menu | Réglage |
|---|---|
| Input Source → Main | HDMI 1 |
| Time | Heure, date, fuseau Paris |
| Schedule | Power ON 07:00, Power OFF 21:30 |
| Brightness | Mode « By Time », jusqu'à 4 paliers |
| Advanced → Screen Protection | ON |
| Advanced → Power Restore Behavior | Power On |

## Sécurité

- Le serveur écoute sur le réseau de l'agence (`VITRINE_HOST=0.0.0.0`). Ne l'exposez pas tel quel sur Internet.
  Pour modifier les contenus depuis l'extérieur, utilisez un accès sécurisé (par exemple Tailscale, ou un tunnel Cloudflare avec connexion).
- Les mots de passe sont stockés chiffrés (scrypt) ; une session dure 7 jours ; un compte est bloqué 10 minutes après 5 échecs.
- Les affiches elles-mêmes (`/files/…`) sont lisibles sans mot de passe par les appareils du réseau : c'est nécessaire pour les écrans.
- Le mot de passe SMTP des alertes est conservé dans la base, sur le NUC.

## Dépannage

- **État du serveur** : `curl http://127.0.0.1:8080/api/status` · **Journal** : `journalctl -u vitrine -f` · **Redémarrer** : `sudo systemctl restart vitrine`
- **Un fichier n'est pas traité** : la fiche du bien affiche l'erreur (PDF protégé ou corrompu, format de vidéo inhabituel).
- **La météo n'apparaît pas** : le NUC doit avoir accès à Internet (Open-Meteo) ; vérifiez qu'aucun rafraîchissement automatique ne recharge la page sur l'écran.
- **Un écran apparaît hors ligne** : il n'a pas interrogé le serveur depuis plus de 2 minutes (écran éteint, Wi-Fi coupé, mauvaise adresse).
- **Sauvegarde / restauration** : copiez le dossier `data/` (base `vitrine.db`, affiches dans `media/`, sauvegardes dans `backups/`).

## Structure du dépôt

```
server/        application (FastAPI) : API, comptes, traitement des PDF, alertes, sauvegardes
web/           page de gestion (HTML/CSS/JS, installable sur téléphone et iPad)
player/        page affichée sur les écrans (diaporama, vues, météo, messages)
scripts/       install.sh, update.sh, kiosk.sh (navigateur plein écran du NUC)
tests/         tests automatiques (.venv/bin/python tests/test_api.py)
config.env.example   réglages du NUC
```
