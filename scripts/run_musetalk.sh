#!/usr/bin/env bash
set -euo pipefail
ROOT="${ROOT:-/workspace/fb-avatar-live}"
export ROOT
exec /workspace/venvs/musetalk/bin/uvicorn services.musetalk_service:app \
  --app-dir "$ROOT" --host 127.0.0.1 --port 18081 --workers 1
