#!/usr/bin/env bash
set -euo pipefail
ROOT="${ROOT:-/workspace/fb-avatar-live}"
export ROOT
cd "$ROOT"
exec "$ROOT/.venv/bin/python" -m fb_avatar_live.forwarder "${1:-facebook}"
