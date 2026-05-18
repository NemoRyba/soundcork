#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$SCRIPT_DIR/soundcork"
PYTHON="$SCRIPT_DIR/.venv/bin/python"
HOST="${SOUNDCORK_HOST:-0.0.0.0}"
PORT="${SOUNDCORK_PORT:-8000}"

if [ ! -x "$PYTHON" ]; then
    echo "Virtualenv Python not found at $PYTHON"
    echo "Create it first with:"
    echo "  python3.12 -m venv .venv"
    echo "  .venv/bin/python -m pip install -r requirements.txt"
    exit 1
fi

echo "Starting SoundCork at http://$HOST:$PORT"
cd "$APP_DIR"
exec "$PYTHON" -m fastapi run main.py --host "$HOST" --port "$PORT"
