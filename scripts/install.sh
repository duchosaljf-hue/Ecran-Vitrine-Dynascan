#!/usr/bin/env bash
# Installation / mise à jour sur le NUC (Ubuntu Desktop 24.04).
#   sudo ./scripts/install.sh            première installation (ou réinstallation complète)
#   sudo ./scripts/install.sh --update   mise à jour après un « git pull » (ne touche ni au système ni à la session)
set -euo pipefail

UPDATE_ONLY=0
[ "${1:-}" = "--update" ] && UPDATE_ONLY=1

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
as_user() { sudo -u "$TARGET_USER" "$@"; }

echo "==> Paquets"
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y \
  python3 python3-venv python3-pip poppler-utils ffmpeg unclutter x11-xserver-utils curl ca-certificates

if [ "$UPDATE_ONLY" -eq 0 ]; then
  echo "==> Navigateur (Google Chrome)"
  if ! command -v google-chrome-stable >/dev/null 2>&1; then
    TMP_DEB="$(mktemp --suffix=.deb)"
    curl -fsSL -o "$TMP_DEB" https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
    apt-get install -y "$TMP_DEB"
    rm -f "$TMP_DEB"
  fi
fi

echo "==> Configuration"
if [ ! -f "$ROOT/config.env" ]; then
  cp "$ROOT/config.env.example" "$ROOT/config.env"
  chown "$TARGET_USER":"$TARGET_USER" "$ROOT/config.env"
  echo "    config.env créé à partir de l'exemple"
fi
# Le serveur doit être joignable depuis le réseau de l'agence (autres écrans, téléphones, PC)
if ! grep -q '^VITRINE_HOST=' "$ROOT/config.env"; then
  printf '\nVITRINE_HOST=0.0.0.0\n' >> "$ROOT/config.env"
fi
mkdir -p "$ROOT/data"
chown -R "$TARGET_USER":"$TARGET_USER" "$ROOT/data"
chmod +x "$ROOT/scripts/"*.sh

echo "==> Environnement Python"
if [ ! -x "$ROOT/.venv/bin/python" ]; then
  as_user python3 -m venv "$ROOT/.venv"
fi
as_user "$ROOT/.venv/bin/pip" install --quiet --upgrade pip
as_user "$ROOT/.venv/bin/pip" install --quiet -r "$ROOT/requirements.txt"

echo "==> Service du serveur (démarrage automatique)"
cat > /etc/systemd/system/vitrine.service <<EOF
[Unit]
Description=Vitrine : serveur de gestion et de diffusion
After=network-online.target
Wants=network-online.target

[Service]
User=$TARGET_USER
WorkingDirectory=$ROOT
ExecStart=$ROOT/.venv/bin/python -m server.vitrine
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable vitrine.service >/dev/null 2>&1
systemctl restart vitrine.service

if [ "$UPDATE_ONLY" -eq 0 ]; then
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
fi

echo "==> Compte administrateur"
as_user "$ROOT/.venv/bin/python" -m server.manage ensure-admin

if [ "$UPDATE_ONLY" -eq 1 ]; then
  # le navigateur de la vitrine est relancé tout seul par kiosk.sh et recharge la nouvelle version
  pkill -u "$TARGET_USER" -f 'google-chrome.*--kiosk' >/dev/null 2>&1 || true
fi

IP="$(hostname -I | awk '{print $1}')"
cat <<MSG

Terminé.

  Page de gestion : http://${IP:-IP-DU-NUC}:8080/admin/
  Écran du NUC    : http://127.0.0.1:8080/
  Autre écran     : http://${IP:-IP-DU-NUC}:8080/?screen=NOM   (voir la page Écrans)

MSG
if [ "$UPDATE_ONLY" -eq 0 ]; then
  echo "Redémarrez le NUC (sudo reboot) quand il est branché sur l'écran DynaScan."
fi
