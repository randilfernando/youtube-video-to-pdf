#!/bin/bash
# Start the web UI. Set HOST=0.0.0.0 to reach it from other devices on your network.
cd "$(dirname "$0")"
HOST=${HOST:-127.0.0.1}
PORT=${PORT:-8000}
echo "Open http://localhost:$PORT"
exec .venv/bin/python -m uvicorn web:app --host "$HOST" --port "$PORT"
