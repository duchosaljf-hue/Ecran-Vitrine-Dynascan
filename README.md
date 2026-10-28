# Écran vitrine DynaScan

Diffusion automatique d'images, de vidéos et de PDF sur l'écran DynaScan DS552LT5-2 de la vitrine
(portrait, 1080×1920), à partir d'un dossier Google Drive partagé avec l'équipe.

```
Dossier Drive « vitrine-contenu »  ──rclone (lecture seule)──►  NUC (Ubuntu)
                                                                  ├─ server/vitrine.py   synchro + playlist + PDF → images
                                                                  └─ Chrome plein écran  → player/ (diaporama, vue météo, messages)
                                                                          │ HDMI 1
                                                                          ▼
                                                                  Écran DynaScan (allumage 7h, extinction 21h30 par l'écran lui-même)
```

## Utilisation quotidienne

Déposez des fichiers dans le dossier Drive `vitrine-contenu`. Dans les 5 minutes, l'écran les affiche.
Supprimez un fichier du dossier : il disparaît de l'écran.

| Type | Formats | Affichage |
|---|---|---|
| Images | JPG, PNG, WebP, GIF | 10 s (modifiable) |
| Vidéos | MP4 (H.264 de préférence), WebM, MOV | jusqu'à la fin |
| PDF | PDF, et Google Docs / Slides (exportés en PDF) | une image par page, 10 s chacune |

Format idéal pour une image ou une page : **1080 × 1920 px** (portrait).

**Ordre** : alphabétique selon le nom, avec les nombres comptés comme tels
(`01-…`, `02-…`, `10-…`). Les sous-dossiers sont acceptés.

**Options dans le nom du fichier** (entre crochets, à placer où l'on veut) :

| Option | Exemple | Effet |
|---|---|---|
| `[duree=N]` | `02-offre [duree=20].png` | affichée N secondes |
| `[debut=AAAA-MM-JJ]` | `soldes [debut=2026-11-01].jpg` | n'apparaît qu'à partir de cette date |
| `[fin=AAAA-MM-JJ]` | `promo [fin=2026-11-30].jpg` | disparaît après cette date (incluse) |

**Bandeau de messages défilants** : un fichier texte nommé `messages.txt` dans le dossier,
un message par ligne (les lignes commençant par `#` sont ignorées).

**Vue météo** : toutes les 3 diapositives, une vue plein écran affiche l'heure, la météo actuelle de
Carquefou et les prévisions des 4 jours suivants (source : Open-Meteo, sans compte). Réglable dans
`config.env` : `WEATHER_EVERY` (0 pour la désactiver), `WEATHER_SECONDS`, `CORNER_CLOCK` (petite horloge en coin).
Sans Internet, la dernière météo connue reste affichée 3 heures, puis la vue est simplement sautée.

## Installation sur le NUC

Prérequis : Ubuntu Desktop 22.04 ou 24.04 installé sur le NUC (en français ou non), connecté au Wi-Fi,
avec un compte utilisateur (par exemple `vitrine`) qui servira à l'ouverture automatique de session.

1. Récupérer le projet sur le NUC :
   ```bash
   sudo apt install -y git
   git clone -b claude/vitrine-player https://github.com/duchosaljf-hue/Ecran-Vitrine-Dynascan.git
   cd Ecran-Vitrine-Dynascan
   ```
2. Installer :
   ```bash
   sudo ./scripts/install.sh
   ```
3. Connecter le NUC au dossier Drive (une seule fois), **avec le compte Google dédié** à la vitrine,
   sur lequel le dossier est partagé en **Lecteur** :
   ```bash
   rclone config
   ```
   Réponses à donner :
   - `n` (nouveau remote), nom : `vitrine`
   - Storage : `drive` (Google Drive)
   - `client_id` et `client_secret` : laisser vide (Entrée)
   - `scope` : `2` (lecture seule : `drive.readonly`)
   - `service_account_file` : laisser vide
   - Configuration avancée : `y`, puis `root_folder_id` :
     `1rrPMDlw66DwGald5EYrK-az0rTv_Bvjy` (les autres questions : Entrée)
   - Auto config : `y` → le navigateur s'ouvre, connectez-vous avec le compte dédié et acceptez
   - Shared Drive (team drive) : `n`
   - Vérifier : `rclone lsf vitrine:` doit lister le contenu du dossier.
4. Redémarrer : `sudo reboot`. Le NUC se connecte seul, lance le serveur puis le navigateur en plein écran.

Si l'image est couchée au lieu d'être droite, changer `SCREEN_ROTATION` dans `config.env`
(`left` ↔ `right`), puis redémarrer.

## Réglages de l'écran DynaScan (télécommande, touche MENU)

| Menu | Réglage |
|---|---|
| Input Source → Main | HDMI 1 |
| Time | Régler l'heure, la date et le fuseau horaire (Paris) |
| Schedule | Power ON 07:00, Power OFF 21:30 |
| Brightness | Mode « By Time », jusqu'à 4 paliers (par exemple 100 % le jour, 60 % le soir) |
| Advanced → Screen Protection | ON (limite l'image rémanente) |
| Advanced → Power Restore Behavior | Power On (déjà par défaut) |

Le NUC peut rester allumé la nuit : il consomme peu, et l'écran en veille ignore le signal.

## Réglages (`config.env`)

Copié depuis `config.env.example` lors de l'installation. Durée par défaut, ville de la météo,
fréquence de synchronisation, etc. Après modification : `sudo systemctl restart vitrine`.

## Dépannage

- **État du serveur** : `curl http://127.0.0.1:8080/api/status` (dernière synchronisation, erreur éventuelle).
- **Température du NUC** : visible dans `/api/status` (champ `temperature` : actuelle et maximum sur 24 h).
  Un avertissement est écrit dans le journal au-delà de `TEMP_WARN_C` (80 °C par défaut). Un NUC qui dépasse
  régulièrement 80 °C a besoin d'un dépoussiérage et d'un emplacement mieux aéré.
- **Journal** : `journalctl -u vitrine -f`
- **Rien ne s'affiche après un ajout** : vérifier que le fichier est bien dans `vitrine-contenu` et
  attendre 5 minutes ; tester `rclone lsf vitrine:`.
- **Internet coupé** : l'écran continue avec la dernière copie locale (`data/inbox`). La météo disparaît
  après 3 heures sans connexion.
- **Tester sans Drive** (sur un PC) : mettre `SYNC_ENABLED=0`, déposer des fichiers dans `data/inbox`,
  lancer `python3 server/vitrine.py`, ouvrir <http://127.0.0.1:8080/>.

## Structure du dépôt

```
server/vitrine.py      serveur (bibliothèque standard Python) : synchro rclone, playlist, conversion des PDF
player/                page web du player (diaporama, fondu, horloge, météo, messages)
scripts/install.sh     installation sur le NUC
scripts/kiosk.sh       lancement du navigateur plein écran, rotation, anti-veille
config.env.example     réglages
```
