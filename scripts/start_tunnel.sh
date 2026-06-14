#!/usr/bin/env bash
# Public tunnel to the local `ats serve` (port 8000) so Feishu can reach the
# approval webhook from the internet. Prints a https://<random>.trycloudflare.com
# URL — paste it into .env as FEISHU_APPROVE_BASE, then run a pead score with
# --channel feishu_bot. No account needed (ephemeral quick tunnel).
#
# Run order:
#   1) ats serve                       # terminal A (webhook on :8000)
#   2) ./scripts/start_tunnel.sh       # terminal B (prints the public URL)
#   3) put the URL in .env: FEISHU_APPROVE_BASE=https://<...>.trycloudflare.com
#   4) ats pead score COHR --channel feishu_bot   # terminal C
set -euo pipefail
PORT="${1:-8000}"
DIR="$(cd "$(dirname "$0")/.." && pwd)"
[ -x "$DIR/var/cloudflared" ] || { echo "var/cloudflared missing — re-download it." >&2; exit 1; }
# Use 127.0.0.1 (not localhost): uvicorn binds IPv4, while `localhost` can resolve
# to IPv6 ::1 on macOS, which cloudflared then fails to reach (Cloudflare 530).
echo "Starting tunnel to http://127.0.0.1:$PORT — copy the https://*.trycloudflare.com URL below."
exec "$DIR/var/cloudflared" tunnel --url "http://127.0.0.1:$PORT"
