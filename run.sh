#!/usr/bin/env bash
# Starts the server and publishes it at this Mac's fixed Tailscale HTTPS address.
set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-8000}"

if ! command -v stockfish >/dev/null; then
  echo "Stockfish missing: brew install stockfish" >&2
  exit 1
fi
[ -f .env ] || echo "No .env yet: copy .env.example to .env and add your JEV_API_KEY." >&2

TS="$(command -v tailscale || echo /Applications/Tailscale.app/Contents/MacOS/Tailscale)"
if [ -x "$TS" ] && "$TS" status >/dev/null 2>&1; then
  # The first time, this prints a link to turn on HTTPS for your tailnet: open it, then run this again.
  "$TS" serve --bg "$PORT" || exit 1
  HOST="$("$TS" status --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')"
  echo "On your iPhone open:  https://$HOST"
  echo "then Share → Add to Home Screen."
else
  echo "Tailscale isn't running, so the phone can't reach this over HTTPS." >&2
  echo "Install it on this Mac and your iPhone (same account), then run this again." >&2
  echo "Serving on http://localhost:$PORT for testing on this Mac only." >&2
fi

# caffeinate keeps the Mac from going to sleep while the server runs (not with the lid closed).
exec caffeinate -i uv run uvicorn chessbot.server:app --host 127.0.0.1 --port "$PORT"
