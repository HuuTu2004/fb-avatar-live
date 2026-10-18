#!/usr/bin/env bash
set -euo pipefail
ROOT="${ROOT:-/workspace/fb-avatar-live}"
cd "$ROOT"
exec "$ROOT/.venv/bin/fb-avatar-live"
