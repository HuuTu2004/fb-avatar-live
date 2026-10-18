#!/usr/bin/env bash
set -euo pipefail
ROOT="${ROOT:-/workspace/fb-avatar-live}"
cd "$ROOT"
if [[ -d "$ROOT/.venv/ortgpu/onnxruntime" ]]; then
  export PYTHONPATH="$ROOT/.venv/ortgpu${PYTHONPATH:+:$PYTHONPATH}"
fi
exec "$ROOT/.venv/bin/fb-avatar-live"
