from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import threading
import uuid
import zipfile
from pathlib import Path

import httpx
import numpy as np
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask


ROOT = Path(os.getenv("ROOT", "/workspace/fb-avatar-live")).resolve()
ENV_PATH = ROOT / ".env"
WEB_ROOT = ROOT / "web"
ASSET_DIR = Path("/workspace/assets")
VOICE_HOME = ROOT / "data" / "voices"
PREVIEW_DIR = ROOT / "data" / "previews"
OPENAI_BASE_URL = "https://api.openai.com/v1"
BUILTIN_VOICES = ["maichi", "baotrang", "kimoanh", "hamy", "giahuy", "huuduc", "quangminh", "tiendat"]
SERVICES = ["fb-avatar-mediamtx", "fb-avatar-musetalk", "fb-avatar-pipeline", "fb-avatar-forwarder"]


class OpenAIPayload(BaseModel):
    api_key: str = ""
    model: str


class VoicePayload(BaseModel):
    voice: str


class PreviewPayload(BaseModel):
    text: str = Field(default="Xin chào, đây là giọng nói thử của bạn.", max_length=240)


class LivePayload(BaseModel):
    comment_source: str = Field(default="demo", pattern="^(facebook|tiktok|both|demo)$")
    fb_object_id: str
    fb_page_access_token: str = ""
    facebook_stream_url: str = ""
    tiktok_unique_id: str = ""
    tiktok_stream_url: str = ""
    poll_seconds: float = Field(default=2, ge=0.5, le=30)
    batch_seconds: float = Field(default=2, ge=0.5, le=30)


class TestPayload(BaseModel):
    message: str = Field(default="Xin chào, hãy giới thiệu ngắn về buổi livestream này.", min_length=1, max_length=500)


class EnvFile:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()

    def ensure(self) -> None:
        if not self.path.exists():
            shutil.copy2(ROOT / ".env.example", self.path)
            self.path.chmod(0o600)

    def read(self) -> dict[str, str]:
        self.ensure()
        values: dict[str, str] = {}
        for raw in self.path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            values[key.strip()] = value
        return values

    def update(self, updates: dict[str, str]) -> None:
        clean: dict[str, str] = {}
        for key, value in updates.items():
            value = str(value).strip()
            if any(char in value for char in "\r\n\0"):
                raise ValueError(f"Invalid value for {key}")
            clean[key] = value
        with self.lock:
            self.ensure()
            lines = self.path.read_text(encoding="utf-8").splitlines()
            found: set[str] = set()
            result: list[str] = []
            for line in lines:
                stripped = line.strip()
                if stripped and not stripped.startswith("#") and "=" in stripped:
                    key = stripped.split("=", 1)[0].strip()
                    if key in clean:
                        result.append(f"{key}={clean[key]}")
                        found.add(key)
                        continue
                result.append(line)
            for key, value in clean.items():
                if key not in found:
                    result.append(f"{key}={value}")
            handle, temp_name = tempfile.mkstemp(prefix=".env-", dir=self.path.parent)
            try:
                with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                    stream.write("\n".join(result).rstrip() + "\n")
                os.chmod(temp_name, 0o600)
                os.replace(temp_name, self.path)
            finally:
                Path(temp_name).unlink(missing_ok=True)


env_file = EnvFile(ENV_PATH)
env_file.ensure()
os.environ.setdefault("ZEROTTS_VOICES_HOME", str(VOICE_HOME))
app = FastAPI(title="Multi-channel Avatar Live Control Panel")
app.mount("/static", StaticFiles(directory=WEB_ROOT / "static"), name="static")
proxy_client: httpx.AsyncClient | None = None
tts_model = None
tts_lock = threading.Lock()


@app.on_event("startup")
async def startup() -> None:
    global proxy_client
    proxy_client = httpx.AsyncClient(timeout=httpx.Timeout(20, connect=5))
    VOICE_HOME.mkdir(parents=True, exist_ok=True)
    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    ASSET_DIR.mkdir(parents=True, exist_ok=True)


@app.on_event("shutdown")
async def shutdown() -> None:
    if proxy_client:
        await proxy_client.aclose()


def _supervisor_status(name: str) -> str:
    result = subprocess.run(["supervisorctl", "status", name], capture_output=True, text=True)
    fields = result.stdout.split()
    if len(fields) > 1:
        return fields[1]
    return "MISSING" if result.returncode else "UNKNOWN"


def _start_or_restart(name: str) -> None:
    state = _supervisor_status(name)
    action = "restart" if state == "RUNNING" else "start"
    result = subprocess.run(["supervisorctl", action, name], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout).strip())


def _stop(name: str) -> None:
    if _supervisor_status(name) == "RUNNING":
        subprocess.run(["supervisorctl", "stop", name], check=True, capture_output=True, text=True)


async def _path_ready() -> bool:
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            response = await client.get("http://127.0.0.1:9997/v3/paths/get/avatar")
            return response.status_code == 200 and response.json().get("ready", False)
    except (httpx.HTTPError, ValueError):
        return False


def _configured(values: dict[str, str]) -> dict[str, bool]:
    uses_openai = values.get("LLM_BASE_URL", "").rstrip("/") == OPENAI_BASE_URL
    return {
        "openai": bool(uses_openai and values.get("LLM_API_KEY") and values.get("LLM_MODEL")),
        "voice": bool(values.get("ZEROTTS_VOICE")),
        "avatar": Path(values.get("AVATAR_VIDEO", "")).is_file(),
        "facebook": bool(values.get("FB_OBJECT_ID") and values.get("FB_PAGE_ACCESS_TOKEN")),
        "facebook_stream": bool(values.get("FACEBOOK_STREAM_URL")),
        "tiktok": bool(values.get("TIKTOK_UNIQUE_ID")),
        "tiktok_stream": bool(values.get("TIKTOK_STREAM_URL")),
    }


@app.get("/", response_class=HTMLResponse)
async def index() -> FileResponse:
    return FileResponse(WEB_ROOT / "index.html")


@app.get("/api/status")
async def status() -> dict:
    values = env_file.read()
    configured = _configured(values)
    return {
        "services": {name.removeprefix("fb-avatar-"): await asyncio.to_thread(_supervisor_status, name) for name in SERVICES},
        "configured": configured,
        "preview_ready": await _path_ready(),
        "voice": values.get("ZEROTTS_VOICE", "maichi"),
        "avatar": values.get("AVATAR_VIDEO", ""),
        "fb_object_id": values.get("FB_OBJECT_ID", ""),
        "comment_source": values.get("COMMENT_SOURCE", "facebook"),
        "tiktok_unique_id": values.get("TIKTOK_UNIQUE_ID", ""),
        "has_fb_token": bool(values.get("FB_PAGE_ACCESS_TOKEN")),
        "has_fb_stream_url": bool(values.get("FACEBOOK_STREAM_URL")),
        "has_tiktok_stream_url": bool(values.get("TIKTOK_STREAM_URL")),
        "openai_model": values.get("LLM_MODEL", "") if configured["openai"] else "",
        "has_openai_key": configured["openai"],
    }


def _saved_openai_key(values: dict[str, str]) -> str:
    if values.get("LLM_BASE_URL", "").rstrip("/") != OPENAI_BASE_URL:
        return ""
    return values.get("LLM_API_KEY", "")


async def _openai_models(api_key: str) -> list[str]:
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(
            f"{OPENAI_BASE_URL}/models",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        if response.status_code >= 400:
            raise HTTPException(status_code=400, detail="OpenAI từ chối API key")
        return sorted(str(item["id"]) for item in response.json().get("data", []) if item.get("id"))


@app.post("/api/openai/test")
async def test_openai(payload: OpenAIPayload) -> dict:
    current = env_file.read()
    api_key = payload.api_key.strip() or _saved_openai_key(current)
    if not api_key:
        raise HTTPException(status_code=400, detail="Hãy nhập OpenAI API key")
    models = await _openai_models(api_key)
    if payload.model and payload.model not in models:
        raise HTTPException(status_code=400, detail="Model không có trong tài khoản OpenAI này")
    return {"ok": True, "models": models}


@app.post("/api/openai/save")
async def save_openai(payload: OpenAIPayload) -> dict:
    current = env_file.read()
    api_key = payload.api_key.strip() or _saved_openai_key(current)
    if not api_key or not payload.model.strip():
        raise HTTPException(status_code=400, detail="Thiếu API key hoặc model")
    models = await _openai_models(api_key)
    if payload.model not in models:
        raise HTTPException(status_code=400, detail="Model không có trong tài khoản OpenAI này")
    env_file.update({"LLM_BASE_URL": OPENAI_BASE_URL, "LLM_API_KEY": api_key, "LLM_MODEL": payload.model})
    if _supervisor_status("fb-avatar-pipeline") == "RUNNING":
        await asyncio.to_thread(_start_or_restart, "fb-avatar-pipeline")
    return {"ok": True, "model": payload.model}


def _voice_items() -> list[dict]:
    from zerotts import installed_voices

    items = [{"id": name, "custom": False} for name in BUILTIN_VOICES]
    for name, path in installed_voices(VOICE_HOME).items():
        meta_path = Path(path) / "meta.json"
        display_name = name
        if meta_path.exists():
            try:
                display_name = json.loads(meta_path.read_text(encoding="utf-8")).get("display_name", name)
            except (OSError, ValueError):
                pass
        items.append({"id": name, "name": display_name, "custom": True})
    return items


@app.get("/api/voices")
async def voices() -> dict:
    return {"voices": await asyncio.to_thread(_voice_items), "selected": env_file.read().get("ZEROTTS_VOICE", "maichi")}


def _validate_voice_zip(path: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        candidates = [name for name in archive.namelist() if name.endswith("voice.npz")]
        if not candidates:
            raise ValueError("ZIP không có voice.npz")
        if len(candidates) > 8:
            raise ValueError("ZIP chứa quá nhiều voice pack")
        for name in candidates:
            if archive.getinfo(name).file_size > 4 * 1024 * 1024:
                raise ValueError("voice.npz quá lớn")
            with archive.open(name) as source:
                data = np.load(io.BytesIO(source.read()), allow_pickle=False)
                emb = data["voice_emb"]
                if emb.dtype != np.float32 or emb.ndim != 3 or emb.shape[0] != 1 or emb.shape[-1] != 768:
                    raise ValueError("Voice latent không tương thích ZeroTTS hiện tại")


async def _save_upload(upload: UploadFile, path: Path, limit: int) -> None:
    total = 0
    with path.open("wb") as target:
        while chunk := await upload.read(1024 * 1024):
            total += len(chunk)
            if total > limit:
                target.close()
                path.unlink(missing_ok=True)
                raise HTTPException(status_code=413, detail="Tệp vượt quá giới hạn")
            target.write(chunk)


@app.post("/api/voices/upload")
async def upload_voice(file: UploadFile = File(...)) -> dict:
    global tts_model
    if Path(file.filename or "").suffix.lower() != ".zip":
        raise HTTPException(status_code=400, detail="ZeroTTS cần voice pack ZIP từ ZeroWeight")
    temp = ROOT / "data" / f"voice-{uuid.uuid4().hex}.zip"
    temp.parent.mkdir(parents=True, exist_ok=True)
    await _save_upload(file, temp, 64 * 1024 * 1024)
    try:
        await asyncio.to_thread(_validate_voice_zip, temp)
        from zerotts import install_voice_zip

        installed = await asyncio.to_thread(install_voice_zip, temp, VOICE_HOME)
        selected = next(iter(installed))
        env_file.update({"ZEROTTS_VOICE": selected, "ZEROTTS_VOICES_HOME": str(VOICE_HOME)})
        with tts_lock:
            tts_model = None
        if _supervisor_status("fb-avatar-pipeline") == "RUNNING":
            await asyncio.to_thread(_start_or_restart, "fb-avatar-pipeline")
        return {"ok": True, "installed": list(installed), "selected": selected}
    except (ValueError, KeyError, zipfile.BadZipFile) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        temp.unlink(missing_ok=True)


@app.post("/api/voices/select")
async def select_voice(payload: VoicePayload) -> dict:
    available = {item["id"] for item in await asyncio.to_thread(_voice_items)}
    if payload.voice not in available:
        raise HTTPException(status_code=404, detail="Không tìm thấy giọng")
    env_file.update({"ZEROTTS_VOICE": payload.voice})
    if _supervisor_status("fb-avatar-pipeline") == "RUNNING":
        await asyncio.to_thread(_start_or_restart, "fb-avatar-pipeline")
    return {"ok": True, "selected": payload.voice}


def _synthesize_preview(text: str) -> Path:
    global tts_model
    from zerotts import ZeroTTS
    from zerotts.text_norm import normalize_vi_text

    values = env_file.read()
    with tts_lock:
        if tts_model is None:
            tts_model = ZeroTTS.from_pretrained(
                values.get("ZEROTTS_MODEL_DIR", "/workspace/models/zerotts"),
                intra_op_num_threads=int(values.get("ZEROTTS_THREADS", "4")),
            )
        audio = tts_model.synthesize(normalize_vi_text(text), voice=values.get("ZEROTTS_VOICE", "maichi"))
        output = PREVIEW_DIR / f"{uuid.uuid4().hex}.wav"
        tts_model.save_audio(audio, output)
        return output


@app.post("/api/voices/preview")
async def preview_voice(payload: PreviewPayload) -> FileResponse:
    output = await asyncio.to_thread(_synthesize_preview, payload.text.strip())
    return FileResponse(output, media_type="audio/wav", background=BackgroundTask(output.unlink, missing_ok=True))


def _normalize_avatar(source: Path, destination: Path) -> dict:
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height,duration", "-of", "json", str(source)],
        check=True,
        capture_output=True,
        text=True,
    )
    streams = json.loads(probe.stdout).get("streams", [])
    if not streams:
        raise ValueError("Tệp không có video")
    values = env_file.read()
    width = int(values.get("OUTPUT_WIDTH", "1280"))
    height = int(values.get("OUTPUT_HEIGHT", "720"))
    fps = int(values.get("OUTPUT_FPS", "25"))
    temp = destination.with_suffix(".working.mp4")
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error", "-i", str(source), "-an",
            "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,fps={fps},format=yuv420p",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-movflags", "+faststart", str(temp),
        ],
        check=True,
    )
    os.replace(temp, destination)
    with destination.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()[:12]
    return {"width": width, "height": height, "fps": fps, "avatar_id": f"live-{digest}"}


@app.post("/api/avatar/upload")
async def upload_avatar(file: UploadFile = File(...)) -> dict:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".mp4", ".mov", ".mkv", ".webm"}:
        raise HTTPException(status_code=400, detail="Avatar phải là MP4, MOV, MKV hoặc WebM")
    source = ROOT / "data" / f"avatar-{uuid.uuid4().hex}{suffix}"
    await _save_upload(file, source, 500 * 1024 * 1024)
    destination = ASSET_DIR / "avatar.mp4"
    try:
        info = await asyncio.to_thread(_normalize_avatar, source, destination)
    except (ValueError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail=f"Không xử lý được video: {exc}") from exc
    finally:
        source.unlink(missing_ok=True)
    env_file.update({"AVATAR_VIDEO": str(destination), "AVATAR_ID": info["avatar_id"]})
    try:
        await asyncio.to_thread(_start_or_restart, "fb-avatar-musetalk")
    except RuntimeError:
        pass
    return {"ok": True, **info}


@app.get("/api/avatar/file")
async def avatar_file() -> FileResponse:
    path = Path(env_file.read().get("AVATAR_VIDEO", ""))
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Chưa có avatar")
    return FileResponse(path, media_type="video/mp4")


@app.post("/api/live/save")
async def save_live(payload: LivePayload) -> dict:
    current = env_file.read()
    token = payload.fb_page_access_token.strip() or current.get("FB_PAGE_ACCESS_TOKEN", "")
    facebook_stream = payload.facebook_stream_url.strip() or current.get("FACEBOOK_STREAM_URL", "")
    tiktok_stream = payload.tiktok_stream_url.strip() or current.get("TIKTOK_STREAM_URL", "")
    env_file.update(
        {
            "COMMENT_SOURCE": payload.comment_source,
            "FB_OBJECT_ID": payload.fb_object_id.strip(),
            "FB_PAGE_ACCESS_TOKEN": token,
            "FACEBOOK_STREAM_URL": facebook_stream,
            "TIKTOK_UNIQUE_ID": payload.tiktok_unique_id.strip().lstrip("@"),
            "TIKTOK_STREAM_URL": tiktok_stream,
            "FB_POLL_SECONDS": str(payload.poll_seconds),
            "FB_BATCH_SECONDS": str(payload.batch_seconds),
            "STREAM_URL": "rtsp://127.0.0.1:8554/avatar",
        }
    )
    for name in ["fb-avatar-pipeline", "fb-avatar-forwarder"]:
        if _supervisor_status(name) == "RUNNING":
            await asyncio.to_thread(_start_or_restart, name)
    return {"ok": True}


def _start_live() -> None:
    values = env_file.read()
    configured = _configured(values)
    missing = [name for name in ("openai", "voice", "avatar") if not configured[name]]
    source = values.get("COMMENT_SOURCE", "facebook")
    if source in {"facebook", "both"} and not configured["facebook"]:
        missing.append("facebook comments")
    if source in {"tiktok", "both"} and not configured["tiktok"]:
        missing.append("tiktok comments")
    if missing:
        raise ValueError("Thiếu cấu hình: " + ", ".join(missing))
    for name in SERVICES:
        _start_or_restart(name)


@app.post("/api/live/start")
async def start_live() -> dict:
    try:
        await asyncio.to_thread(_start_live)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "message": "Các dịch vụ đang khởi động"}


@app.post("/api/live/stop")
async def stop_live() -> dict:
    for name in ["fb-avatar-forwarder", "fb-avatar-pipeline", "fb-avatar-musetalk"]:
        await asyncio.to_thread(_stop, name)
    return {"ok": True}


@app.post("/api/live/test")
async def test_live(payload: TestPayload) -> dict:
    from fb_avatar_live.core import Comment, CommentStore

    values = env_file.read()
    store = CommentStore(Path(values.get("DATA_DIR", ROOT / "data")) / "comments.sqlite3")
    try:
        identifier = f"dashboard-{uuid.uuid4().hex}"
        await asyncio.to_thread(store.add, [Comment(identifier, "Dashboard", payload.message.strip())])
    finally:
        store.close()
    return {"ok": True, "message": "Đã đưa câu thử vào hàng đợi"}


@app.api_route("/preview/{path:path}", methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"])
async def preview_proxy(path: str, request: Request) -> Response:
    if proxy_client is None:
        raise HTTPException(status_code=503, detail="Proxy chưa sẵn sàng")
    target = f"http://127.0.0.1:8889/{path}"
    if request.url.query:
        target += "?" + request.url.query
    headers = {key: value for key, value in request.headers.items() if key.lower() not in {"host", "content-length"}}
    try:
        upstream = await proxy_client.request(request.method, target, content=await request.body(), headers=headers)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail="WebRTC gateway chưa chạy") from exc
    response_headers = {}
    for key in ("content-type", "cache-control", "etag", "accept-post", "accept-patch", "link"):
        if key in upstream.headers:
            response_headers[key] = upstream.headers[key]
    for key, value in upstream.headers.items():
        if key.lower().startswith("access-control-"):
            response_headers[key] = value
    location = upstream.headers.get("location")
    if location:
        if location.startswith("http://127.0.0.1:8889"):
            location = "/preview" + location.removeprefix("http://127.0.0.1:8889")
        elif location.startswith("/"):
            location = "/preview" + location
        response_headers["location"] = location
    return Response(upstream.content, status_code=upstream.status_code, headers=response_headers)
