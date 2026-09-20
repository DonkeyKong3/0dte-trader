#!/usr/bin/env bash
# One-command local run for macOS/Linux.
# Creates the venv on first run, installs/updates deps, then starts the
# dashboard bound to all network interfaces so it's reachable from your
# phone over the same Wi-Fi.
set -e
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
fi

echo "Installing/updating dependencies..."
.venv/bin/pip install -q -r requirements.txt

IP=$(ipconfig getifaddr en0 2>/dev/null || hostname -I 2>/dev/null | awk '{print $1}')

echo ""
echo "Starting dashboard:"
echo "  This machine: http://localhost:8000"
if [ -n "$IP" ]; then
    echo "  Phone/LAN:    http://$IP:8000   (same Wi-Fi, e.g. from your iPhone)"
fi
echo "  Press Ctrl+C to stop."
echo ""

.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
