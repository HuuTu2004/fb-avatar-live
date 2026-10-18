#!/usr/bin/env bash
set -euo pipefail
ROOT="${ROOT:-/workspace/fb-avatar-live}"
exec /usr/local/bin/mediamtx "$ROOT/config/mediamtx.yml"
