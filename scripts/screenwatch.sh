#!/usr/bin/env bash
# Relance automatique des écrans Android (Canlarriz…) qui ne diffusent plus, par ADB.
# Lancé toutes les 2 minutes par vitrine-screenwatch.timer (compte de la vitrine).
#
# Réglage dans config.env (un ou plusieurs écrans, séparés par des espaces) :
#   ADB_SCREENS="canlarriz=192.168.1.16:5555"
# Un écran est jugé arrêté quand sa page n'a pas interrogé la vitrine depuis 4 minutes :
#   1er, 2e et 3e contrôle : on relance Magic Player (la diffusion reprend seule)
#   4e contrôle            : on redémarre l'écran, au plus une fois toutes les 2 heures
# Pour faire des réglages sur un écran sans être interrompu : touch data/screenwatch/pause (effet 2 heures).
# Les commandes peuvent être remplacées par des variables d'environnement (pour les tests).
set -u

ROOT="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"

cfg() {  # valeur d'un réglage : variable d'environnement, sinon config.env
  local value="${!1:-}"
  if [ -z "$value" ] && [ -f "$ROOT/config.env" ]; then
    value="$(grep -E "^$1=" "$ROOT/config.env" | tail -n 1 | cut -d= -f2- | sed -e 's/^["'\'']//' -e 's/["'\'']$//')"
  fi
  printf '%s' "${value:-$2}"
}

SCREENS="$(cfg ADB_SCREENS "")"
[ -n "$SCREENS" ] || exit 0
PKG="$(cfg ADB_PLAYER_PACKAGE cn.huidu.lcd.player)"
PORT="$(cfg VITRINE_PORT 8080)"
STATE_DIR="${SCREENWATCH_STATE_DIR:-$ROOT/data/screenwatch}"
ADB="${SCREENWATCH_ADB:-adb}"
STATUS_CMD="${SCREENWATCH_STATUS_CMD:-curl -fsS --max-time 10 http://127.0.0.1:$PORT/api/status}"
NOTIFY="${SCREENWATCH_NOTIFY:-$ROOT/.venv/bin/python -m server.manage notify}"
NOW="${SCREENWATCH_NOW:-$(date +%s)}"
OFFLINE_AFTER=240
REBOOT_MIN_GAP=$((2 * 3600))

mkdir -p "$STATE_DIR"
log() { logger -t vitrine-screenwatch "$*" 2>/dev/null || true; echo "vitrine-screenwatch: $*"; }

if [ -f "$STATE_DIR/pause" ]; then
  paused_at="$(stat -c %Y "$STATE_DIR/pause" 2>/dev/null || echo 0)"
  if [ $((NOW - paused_at)) -lt 7200 ]; then exit 0; fi
  rm -f "$STATE_DIR/pause"
fi

status="$($STATUS_CMD 2>/dev/null)" || exit 0   # serveur de la vitrine indisponible : rien à juger

for entry in $SCREENS; do
  slug="${entry%%=*}"
  target="${entry#*=}"
  # secondes depuis le dernier passage de la page de cet écran (-1 : jamais vu)
  age="$(python3 -c '
import json, sys, datetime
seen = json.loads(sys.argv[1]).get("screens", {}).get(sys.argv[2])
if not seen:
    print(-1)
else:
    t = datetime.datetime.strptime(seen, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
    print(int(sys.argv[3]) - int(t.timestamp()))
' "$status" "$slug" "$NOW" 2>/dev/null || echo -1)"
  fails_file="$STATE_DIR/$slug.fails"
  if [ "$age" -lt 0 ]; then continue; fi
  if [ "$age" -lt "$OFFLINE_AFTER" ]; then
    if [ -s "$fails_file" ]; then log "$slug : diffusion reprise"; fi
    : > "$fails_file"
    continue
  fi

  fails=$(( $(cat "$fails_file" 2>/dev/null || echo 0) + 1 ))
  echo "$fails" > "$fails_file"
  $ADB connect "$target" >/dev/null 2>&1
  if ! $ADB -s "$target" shell true >/dev/null 2>&1; then
    log "$slug : écran injoignable par ADB ($target), éteint ou hors Wi-Fi"
    continue
  fi

  if [ "$fails" -le 3 ]; then
    $ADB -s "$target" shell am force-stop "$PKG" >/dev/null 2>&1
    sleep "${SCREENWATCH_SLEEP:-2}"
    $ADB -s "$target" shell monkey -p "$PKG" -c android.intent.category.LAUNCHER 1 >/dev/null 2>&1
    log "$slug : Magic Player relancé (tentative $fails)"
    if [ "$fails" -eq 1 ]; then
      $NOTIFY "Écran relancé automatiquement : $slug" \
        "La page de l'écran « $slug » ne répondait plus depuis $((age / 60)) minutes : Magic Player a été relancé automatiquement par le NUC. Aucune action n'est nécessaire si l'écran repasse en ligne." >/dev/null 2>&1
    fi
  else
    last="$(cat "$STATE_DIR/$slug.last-reboot" 2>/dev/null || echo 0)"
    if [ $((NOW - last)) -ge "$REBOOT_MIN_GAP" ]; then
      echo "$NOW" > "$STATE_DIR/$slug.last-reboot"
      : > "$fails_file"
      $ADB -s "$target" reboot >/dev/null 2>&1
      log "$slug : relances sans effet, redémarrage de l'écran"
      $NOTIFY "Écran redémarré automatiquement : $slug" \
        "La relance de Magic Player n'a pas suffi : l'écran « $slug » a été redémarré par le NUC. Il reprend la diffusion seul en 2 minutes environ." >/dev/null 2>&1
    fi
  fi
done
exit 0
