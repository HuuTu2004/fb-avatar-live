#!/usr/bin/env bash
set -euo pipefail
ROOT="${ROOT:-/workspace/fb-avatar-live}"
export ROOT
cd "$ROOT"
exec "$ROOT/.venv/bin/uvicorn" services.control_panel:app \
  --app-dir "$ROOT" --host 127.0.0.1 --port 18080 --workers 1
