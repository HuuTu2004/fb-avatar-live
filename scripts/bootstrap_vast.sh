#!/usr/bin/env bash
set -euo pipefail

ROOT="${ROOT:-/workspace/fb-avatar-live}"
MUSETALK_ROOT="${MUSETALK_ROOT:-/workspace/MuseTalk}"
ZEROTTS_ROOT="${ZEROTTS_ROOT:-/workspace/ZeroTTS}"
MODEL_ROOT="${MODEL_ROOT:-/workspace/models/zerotts}"
MEDIAMTX_VERSION="${MEDIAMTX_VERSION:-1.21.1}"

mkdir -p /workspace/venvs /workspace/models

if [[ ! -d "$MUSETALK_ROOT/.git" ]]; then
  git clone --depth 1 https://github.com/TMElyralab/MuseTalk.git "$MUSETALK_ROOT"
fi
if [[ ! -d "$ZEROTTS_ROOT/.git" ]]; then
  git clone --depth 1 https://github.com/zeroweight-ai/ZeroTTS.git "$ZEROTTS_ROOT"
fi

uv venv --python 3.11 "$ROOT/.venv"
uv pip install --python "$ROOT/.venv/bin/python" -e "$ROOT"
# The pipeline adds this target directory to PYTHONPATH for CUDA ZeroTTS.
uv pip install --python "$ROOT/.venv/bin/python" --target "$ROOT/.venv/ortgpu" \
  --no-deps onnxruntime-gpu==1.30.0
"$ROOT/.venv/bin/hf" download zeroweight-ai/ZeroTTS --local-dir "$MODEL_ROOT"

case "$(uname -m)" in
  x86_64) mediamtx_arch=amd64 ;;
  aarch64|arm64) mediamtx_arch=arm64v8 ;;
  *) echo "Unsupported MediaMTX architecture: $(uname -m)" >&2; exit 1 ;;
esac
mediamtx_archive="/tmp/mediamtx-${MEDIAMTX_VERSION}.tar.gz"
curl -fL "https://github.com/bluenviron/mediamtx/releases/download/v${MEDIAMTX_VERSION}/mediamtx_v${MEDIAMTX_VERSION}_linux_${mediamtx_arch}.tar.gz" -o "$mediamtx_archive"
tar -xzf "$mediamtx_archive" -C /tmp mediamtx
install -m 755 /tmp/mediamtx /usr/local/bin/mediamtx

uv venv --python 3.10 /workspace/venvs/musetalk
uv pip install --python /workspace/venvs/musetalk/bin/python \
  torch==2.0.1 torchvision==0.15.2 torchaudio==2.0.2 \
  --index-url https://download.pytorch.org/whl/cu118
uv pip install --python /workspace/venvs/musetalk/bin/python xformers==0.0.20
uv pip install --python /workspace/venvs/musetalk/bin/python -r "$MUSETALK_ROOT/requirements.txt"
uv pip install --python /workspace/venvs/musetalk/bin/python openmim
source /workspace/venvs/musetalk/bin/activate
mim install mmengine
mim install 'mmcv==2.0.1'
mim install 'mmdet==3.1.0'
python -m pip install --no-build-isolation chumpy==0.70
mim install 'mmpose==1.1.0'

cd "$MUSETALK_ROOT"
mkdir -p models/{musetalkV15,syncnet,dwpose,face-parse-bisent,sd-vae,whisper}
huggingface-cli download TMElyralab/MuseTalk --local-dir models \
  --include 'musetalkV15/musetalk.json' 'musetalkV15/unet.pth'
huggingface-cli download stabilityai/sd-vae-ft-mse --local-dir models/sd-vae \
  --include 'config.json' 'diffusion_pytorch_model.bin'
huggingface-cli download openai/whisper-tiny --local-dir models/whisper \
  --include 'config.json' 'pytorch_model.bin' 'preprocessor_config.json'
huggingface-cli download yzd-v/DWPose --local-dir models/dwpose --include 'dw-ll_ucoco_384.pth'
huggingface-cli download ByteDance/LatentSync --local-dir models/syncnet --include 'latentsync_syncnet.pt'
[[ -s models/face-parse-bisent/79999_iter.pth ]] || \
  gdown 154JgKpzCPW82qINcVieuPH3fZ2e0P812 -O models/face-parse-bisent/79999_iter.pth
[[ -s models/face-parse-bisent/resnet18-5c106cde.pth ]] || \
  curl -fL https://download.pytorch.org/models/resnet18-5c106cde.pth \
  -o models/face-parse-bisent/resnet18-5c106cde.pth

echo "Bootstrap complete. Copy .env.example to .env and add credentials."
