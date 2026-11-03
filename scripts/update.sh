#!/usr/bin/env bash
# Mise à jour : récupère la dernière version du projet, puis relance tout.
#   ./scripts/update.sh
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
git pull --ff-only
exec sudo "$ROOT/scripts/install.sh" --update
