#!/usr/bin/env bash
# Surveillance du réseau du NUC, lancée toutes les 3 minutes par vitrine-netwatch.timer (en root).
# Le NUC doit joindre la box : sinon plus de page pour les écrans Wi-Fi, ni d'accès à distance, ni d'alerte e-mail.
#   1 échec  : on note
#   2 échecs : on relance le Wi-Fi
#   3 échecs : on relance NetworkManager
#   6 échecs (≈ 18 min) : on redémarre le NUC, au plus une fois toutes les 6 heures
# Les commandes peuvent être remplacées par des variables d'environnement (pour les tests).
set -u

STATE_DIR="${NETWATCH_STATE_DIR:-/var/lib/vitrine-netwatch}"
PING="${NETWATCH_PING:-ping}"
NMCLI="${NETWATCH_NMCLI:-nmcli}"
SYSTEMCTL="${NETWATCH_SYSTEMCTL:-systemctl}"
REBOOT="${NETWATCH_REBOOT:-systemctl reboot}"
GATEWAY_CMD="${NETWATCH_GATEWAY_CMD:-ip route show default}"
NOW="${NETWATCH_NOW:-$(date +%s)}"
REBOOT_MIN_GAP=$((6 * 3600))

mkdir -p "$STATE_DIR"
log() { logger -t vitrine-netwatch "$*" 2>/dev/null || echo "vitrine-netwatch: $*"; }

gateway="$($GATEWAY_CMD 2>/dev/null | awk '/default/ {print $3; exit}')"
if [ -n "$gateway" ] && $PING -c 2 -W 2 "$gateway" >/dev/null 2>&1; then
  if [ -s "$STATE_DIR/fails" ]; then log "réseau rétabli"; fi
  : > "$STATE_DIR/fails"
  exit 0
fi

fails=$(( $(cat "$STATE_DIR/fails" 2>/dev/null || echo 0) + 1 ))
echo "$fails" > "$STATE_DIR/fails"
log "box injoignable (${gateway:-pas de route}) : échec n°$fails"

case "$fails" in
  2) $NMCLI radio wifi off >/dev/null 2>&1; sleep 2; $NMCLI radio wifi on >/dev/null 2>&1; log "Wi-Fi relancé" ;;
  3) $SYSTEMCTL restart NetworkManager >/dev/null 2>&1; log "NetworkManager relancé" ;;
esac

if [ "$fails" -ge 6 ]; then
  last="$(cat "$STATE_DIR/last-reboot" 2>/dev/null || echo 0)"
  if [ $((NOW - last)) -ge "$REBOOT_MIN_GAP" ]; then
    echo "$NOW" > "$STATE_DIR/last-reboot"
    : > "$STATE_DIR/fails"
    log "réseau toujours absent : redémarrage du NUC"
    $REBOOT
  fi
fi
exit 0
