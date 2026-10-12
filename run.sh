#!/usr/bin/env bash
# pt-usage-dashboard one-click: fetch data -> generate dashboard
# Usage:
#   ./run.sh          # English dashboard (default)
#   ./run.sh zh       # Chinese dashboard
# Requires: Python 3.8+ (standard library only), config.json with your _Bearer token
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PYTHON:-python3}"
LANG_CHOICE="${1:-en}"

echo "==> [1/2] Fetch PowerTokens usage (needs config.json with _Bearer token)"
if [ -f "$DIR/config.json" ]; then
  "$PY" "$DIR/scripts/fetch_pt_server.py" \
    || echo "!! fetch failed (token may be expired) — will try cached pt_usage_server*.json"
else
  echo "!! no config.json found — skip fetch, will use cached pt_usage_server*.json if any"
  echo "   (copy config.example.json to config.json and paste your _Bearer cookie value)"
fi

echo "==> [2/2] Generate dashboard (lang=$LANG_CHOICE)"
"$PY" "$DIR/scripts/gen_dashboard.py" --lang "$LANG_CHOICE" --out "$DIR/pt-usage-dashboard.html"

echo "==> Done. Open in your browser:"
echo "    $DIR/pt-usage-dashboard.html"
