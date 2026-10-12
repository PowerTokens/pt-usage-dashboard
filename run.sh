#!/usr/bin/env bash
# pt-usage-dashboard one-click launcher
#
#   ./run.sh            # 打开本地网页控制台（推荐：粘贴 token、拉数据、看板都在浏览器里）
#   ./run.sh web        # 同上
#   ./run.sh cli        # 老方式：命令行拉取 + 生成 HTML（不自动开浏览器）
#   ./run.sh cli zh     # 老方式并指定中文
#
# Requires: Python 3.8+ (standard library only)
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PYTHON:-python3}"
MODE="${1:-web}"
LANG_CHOICE="${2:-en}"

if [ "$MODE" = "cli" ]; then
  echo "==> [1/2] Fetch PowerTokens usage (needs config.json with _Bearer token)"
  if [ -f "$DIR/config.json" ]; then
    "$PY" "$DIR/scripts/fetch_pt_server.py" \
      || echo "!! fetch failed (token may be expired) — will try cached pt_usage_server*.json"
  else
    echo "!! no config.json found — skip fetch, will use cached pt_usage_server*.json if any"
    echo "   (or just run './run.sh' with no args to paste your token in the browser)"
  fi
  echo "==> [2/2] Generate dashboard (lang=$LANG_CHOICE)"
  "$PY" "$DIR/scripts/gen_dashboard.py" --lang "$LANG_CHOICE" --out "$DIR/pt-usage-dashboard.html"
  echo "==> Done. Open in your browser:"
  echo "    $DIR/pt-usage-dashboard.html"
  exit 0
fi

echo "==> 启动本地网页控制台（只监听本机，token 不出这台电脑）"
echo "    浏览器会自动打开；没自动开就手动访问 http://127.0.0.1:8777/"
echo "    Ctrl+C 停止。"
exec "$PY" "$DIR/serve.py"
