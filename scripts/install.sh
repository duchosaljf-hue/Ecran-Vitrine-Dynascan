#!/usr/bin/env bash
# Installation sur le NUC (Ubuntu Desktop 22.04 ou 24.04).
# Usage : sudo ./scripts/install.sh      (depuis le dossier du dépôt cloné sur le NUC)
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Lancez ce script avec sudo : sudo ./scripts/install.sh" >&2
  exit 1
fi

TARGET_USER="${SUDO_USER:-}"
if [ -z "$TARGET_USER" ] || [ "$TARGET_USER" = "root" ]; then
  echo "Lancez-le avec sudo depuis le compte utilisateur qui ouvrira la session (pas en root direct)." >&2
  exit 1
fi
TARGET_HOME="$(getent passwd "$TARGET_USER" | cut -d: -f6)"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "==> Paquets"
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y \
  python3 poppler-utils rclone ffmpeg unclutter x11-xserver-utils curl ca-certificates

echo "==> Navigateur (Google Chrome)"
if ! command -v google-chrome-stable >/dev/null 2>&1; then
  TMP_DEB="$(mktemp --suffix=.deb)"
  curl -fsSL -o "$TMP_DEB" https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
  apt-get install -y "$TMP_DEB"
  rm -f "$TMP_DEB"
fi

echo "==> Configuration"
if [ ! -f "$ROOT/config.env" ]; then
  cp "$ROOT/config.env.example" "$ROOT/config.env"
  chown "$TARGET_USER":"$TARGET_USER" "$ROOT/config.env"
  echo "    config.env créé à partir de l'exemple"
fi
mkdir -p "$ROOT/data/inbox" "$ROOT/data/cache"
chown -R "$TARGET_USER":"$TARGET_USER" "$ROOT/data"
chmod +x "$ROOT/server/vitrine.py" "$ROOT/scripts/"*.sh

echo "==> Service du serveur (démarrage automatique)"
cat > /etc/systemd/system/vitrine.service <<EOF
[Unit]
Description=Vitrine : serveur de diffusion
After=network-online.target
Wants=network-online.target

[Service]
User=$TARGET_USER
WorkingDirectory=$ROOT
ExecStart=/usr/bin/python3 $ROOT/server/vitrine.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now vitrine.service

echo "==> Lancement automatique du player à l'ouverture de session"
AUTOSTART_DIR="$TARGET_HOME/.config/autostart"
mkdir -p "$AUTOSTART_DIR"
cat > "$AUTOSTART_DIR/vitrine-kiosk.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Vitrine
Exec=$ROOT/scripts/kiosk.sh
X-GNOME-Autostart-enabled=true
EOF
chown -R "$TARGET_USER":"$TARGET_USER" "$TARGET_HOME/.config"

echo "==> Session automatique, sans Wayland (nécessaire pour la rotation de l'écran)"
GDM_CONF=/etc/gdm3/custom.conf
if [ -f "$GDM_CONF" ]; then
  sed -i -E '/^#?\s*WaylandEnable=/d; /^#?\s*AutomaticLoginEnable=/d; /^#?\s*AutomaticLogin=/d' "$GDM_CONF"
  sed -i "/^\[daemon\]/a WaylandEnable=false\nAutomaticLoginEnable=true\nAutomaticLogin=$TARGET_USER" "$GDM_CONF"
else
  echo "    /etc/gdm3/custom.conf introuvable : activez la connexion automatique dans les paramètres Ubuntu."
fi

echo "==> Pas de mise en veille du système"
systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target

cat <<MSG

Installation terminée.

Étapes restantes (voir README.md) :
  1. Connecter rclone au dossier Drive :  rclone config
  2. Ajuster config.env si besoin (rotation, durées...)
  3. Redémarrer le NUC :  sudo reboot
MSG
