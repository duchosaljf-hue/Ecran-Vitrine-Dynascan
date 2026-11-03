#!/usr/bin/env bash
# Lancé à l'ouverture de session graphique (autologin) : tourne l'écran, empêche
# la mise en veille et affiche le player en plein écran. Relance le navigateur s'il se ferme.
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[ -f "$ROOT/config.env" ] && set -a && . "$ROOT/config.env" && set +a

URL="http://127.0.0.1:${VITRINE_PORT:-8080}/"
ROTATION="${SCREEN_ROTATION:-left}"
PROFILE="$ROOT/data/browser-profile"

# Navigateur disponible
BROWSER=""
for candidate in google-chrome-stable google-chrome chromium chromium-browser; do
  if command -v "$candidate" >/dev/null 2>&1; then BROWSER="$candidate"; break; fi
done
[ -z "$BROWSER" ] && { echo "Aucun navigateur trouvé (Chrome/Chromium)"; exit 1; }

# Pas de mise en veille ni d'économiseur d'écran (l'extinction nocturne est gérée par l'écran)
xset s off -dpms s noblank 2>/dev/null || true
gsettings set org.gnome.desktop.session idle-delay 0 2>/dev/null || true
gsettings set org.gnome.desktop.screensaver lock-enabled false 2>/dev/null || true
gsettings set org.gnome.desktop.screensaver idle-activation-enabled false 2>/dev/null || true
gsettings set org.gnome.settings-daemon.plugins.power sleep-inactive-ac-type 'nothing' 2>/dev/null || true
command -v unclutter >/dev/null 2>&1 && (unclutter -idle 1 -root &) 2>/dev/null

# Attente du serveur (jusqu'à 2 minutes)
for _ in $(seq 1 60); do
  curl -fs "${URL}api/status" >/dev/null 2>&1 && break
  sleep 2
done

# Rotation portrait de la première sortie connectée (X11 uniquement)
if command -v xrandr >/dev/null 2>&1; then
  OUTPUT="$(xrandr | awk '/ connected/{print $1; exit}')"
  [ -n "$OUTPUT" ] && xrandr --output "$OUTPUT" --rotate "$ROTATION" || true
fi

mkdir -p "$PROFILE"
while true; do
  # Évite le bandeau « Chrome ne s'est pas fermé correctement »
  sed -i 's/"exited_cleanly":false/"exited_cleanly":true/; s/"exit_type":"Crashed"/"exit_type":"Normal"/' \
    "$PROFILE/Default/Preferences" 2>/dev/null || true
  "$BROWSER" \
    --kiosk \
    --user-data-dir="$PROFILE" \
    --noerrdialogs --disable-infobars --disable-session-crashed-bubble \
    --no-first-run --no-default-browser-check \
    --disable-translate --disable-features=Translate,TranslateUI \
    --autoplay-policy=no-user-gesture-required \
    --overscroll-history-navigation=0 --disable-pinch \
    --password-store=basic \
    "$URL"
  sleep 3
done
