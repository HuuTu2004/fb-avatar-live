from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
import uuid
import zipfile
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import httpx
import numpy as np
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from fb_avatar_live.config import ReplySettings, read_reply_settings
from fb_avatar_live.streamer import write_commerce_label


ROOT = Path(os.getenv("ROOT", "/workspace/fb-avatar-live")).resolve()
ENV_PATH = ROOT / ".env"
WEB_ROOT = ROOT / "web"
ASSET_DIR = ROOT / "assets" if os.name == "nt" else Path("/workspace/assets")
VOICE_HOME = ROOT / "data" / "voices"
PREVIEW_DIR = ROOT / "data" / "previews"
OPENAI_BASE_URL = "https://api.openai.com/v1"
BUILTIN_VOICES = ["maichi", "baotrang", "kimoanh", "hamy", "giahuy", "huuduc", "quangminh", "tiendat"]
SERVICES = ["fb-avatar-mediamtx", "fb-avatar-musetalk", "fb-avatar-pipeline", "fb-avatar-forwarder"]
FACEBOOK_SERVER_URL = "rtmps://live-api-s.facebook.com:443/rtmp/"
FORWARDER_HEALTH_DIR = Path("/tmp")


class OpenAIPayload(BaseModel):
    api_key: str = ""
    model: Literal["gpt-6-luna"] = "gpt-6-luna"


class PauseAIPayload(BaseModel):
    paused: bool


class AffiliatePayload(BaseModel):
    cart_confirmed: bool


class VoicePayload(BaseModel):
    voice: str


class PreviewPayload(BaseModel):
    text: str = Field(default="Xin chào, đây là giọng nói thử của bạn.", max_length=240)


class LivePayload(BaseModel):
    comment_source: str = Field(default="demo", pattern="^(facebook|facebook_personal|tiktok|both|demo)$")
    fb_object_id: str = ""
    fb_page_access_token: str = ""
    ssn_session_id: str = ""
    facebook_server_url: str = ""
    facebook_stream_key: str = ""
    facebook_stream_url: str = ""
    tiktok_unique_id: str = ""
    poll_seconds: float = Field(default=2, ge=0.5, le=30)
    batch_seconds: float = Field(default=2, ge=0.5, le=30)
    demo_sales_mode: bool = False


class TestPayload(BaseModel):
    message: str = Field(default="Vô thường là gì?", min_length=1, max_length=500)


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
    try:
        result = subprocess.run(["supervisorctl", "status", name], capture_output=True, text=True)
    except FileNotFoundError:
        return "MISSING"
    fields = result.stdout.split()
    if len(fields) > 1:
        return fields[1]
    return "MISSING" if result.returncode else "UNKNOWN"


def _forwarder_health(channel: str, supervisor_state: str) -> str:
    if supervisor_state != "RUNNING":
        return "stopped"
    try:
        data = json.loads((FORWARDER_HEALTH_DIR / f"fb-avatar-forwarder-{channel}.json").read_text(encoding="utf-8"))
        if time.time() - float(data["updated_at"]) <= 15 and data.get("state") in {"starting", "connecting", "sending", "retrying", "unconfigured"}:
            return data["state"]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return "unknown"


def _start_or_restart(name: str) -> None:
    state = _supervisor_status(name)
    action = "restart" if state == "RUNNING" else "start"
    try:
        result = subprocess.run(["supervisorctl", action, name], capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise RuntimeError("Máy này chưa có Supervisor để điều khiển live. Chạy các dịch vụ live trên máy Linux/Vast.") from exc
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
        "openai": bool(uses_openai and values.get("LLM_API_KEY")),
        "voice": bool(values.get("ZEROTTS_VOICE")),
        "avatar": Path(values.get("AVATAR_VIDEO", "")).is_file(),
        "facebook": bool(values.get("FB_OBJECT_ID") and values.get("FB_PAGE_ACCESS_TOKEN")),
        "facebook_personal": bool(values.get("SSN_SESSION_ID")),
        "facebook_stream": bool(values.get("FACEBOOK_STREAM_URL")),
        "tiktok": bool(values.get("TIKTOK_UNIQUE_ID")),
    }


def _facebook_server(destination: str) -> str:
    return destination.rsplit("/", 1)[0] + "/" if destination else FACEBOOK_SERVER_URL


def _facebook_destination(server: str, key: str, existing: str) -> str:
    server = (server or _facebook_server(existing)).strip().rstrip("/") + "/"
    parsed = urlsplit(server)
    if parsed.scheme != "rtmps" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("URL máy chủ Facebook phải là RTMPS hợp lệ")
    key = key.strip() or (existing.rsplit("/", 1)[-1] if existing else "")
    if not key:
        return ""
    if "/" in key or any(char.isspace() for char in key):
        raise ValueError("Khóa luồng Facebook không hợp lệ")
    return server + key


@app.get("/", response_class=HTMLResponse)
async def index() -> FileResponse:
    return FileResponse(WEB_ROOT / "index.html")


@app.get("/api/status")
async def status() -> dict:
    values = env_file.read()
    configured = _configured(values)
    services = {name.removeprefix("fb-avatar-"): await asyncio.to_thread(_supervisor_status, name) for name in SERVICES}
    return {
        "services": services,
        "forwarder_health": {
            "facebook": _forwarder_health("facebook", services["forwarder"]),
        },
        "configured": configured,
        "preview_ready": await _path_ready(),
        "voice": values.get("ZEROTTS_VOICE", "maichi"),
        "avatar": values.get("AVATAR_VIDEO", ""),
        "fb_object_id": values.get("FB_OBJECT_ID", ""),
        "comment_source": values.get("COMMENT_SOURCE", "facebook"),
        "demo_sales_mode": values.get("DEMO_SALES_MODE", "false").lower() in {"1", "true", "yes", "on"},
        "poll_seconds": values.get("FB_POLL_SECONDS", "2"),
        "batch_seconds": values.get("FB_BATCH_SECONDS", "2"),
        "tiktok_unique_id": values.get("TIKTOK_UNIQUE_ID", ""),
        "has_fb_token": bool(values.get("FB_PAGE_ACCESS_TOKEN")),
        "has_ssn_session_id": bool(values.get("SSN_SESSION_ID")),
        "has_fb_stream_url": bool(values.get("FACEBOOK_STREAM_URL")),
        "facebook_server_url": values.get("FACEBOOK_SERVER_URL") or _facebook_server(values.get("FACEBOOK_STREAM_URL", "")),
        "openai_model": "gpt-6-luna" if configured["openai"] else "",
        "has_openai_key": configured["openai"],
        "ai_speech_paused": values.get("PAUSE_AI_SPEECH", "false").lower() in {"true", "1", "yes", "on"},
        "affiliate_cart_confirmed": values.get("AFFILIATE_CART_CONFIRMED", "false").lower() in {"true", "1", "yes", "on"},
    }


@app.post("/api/ai/pause")
async def pause_ai_speech(payload: PauseAIPayload) -> dict:
    env_file.update({"PAUSE_AI_SPEECH": "true" if payload.paused else "false"})
    return {"ok": True, "paused": payload.paused}


@app.get("/api/affiliate")
async def affiliate_settings() -> dict:
    value = env_file.read().get("AFFILIATE_CART_CONFIRMED", "false")
    return {"cart_confirmed": value.lower() in {"true", "1", "yes", "on"}}


@app.post("/api/affiliate")
async def save_affiliate_settings(payload: AffiliatePayload) -> dict:
    env_file.update({"AFFILIATE_CART_CONFIRMED": "true" if payload.cart_confirmed else "false"})
    values = env_file.read()
    data_dir = Path(values.get("DATA_DIR", ROOT / "data"))
    if not data_dir.is_absolute():
        data_dir = ROOT / data_dir
    write_commerce_label(data_dir, payload.cart_confirmed)
    return {"ok": True, "cart_confirmed": payload.cart_confirmed}


@app.get("/api/replies")
async def reply_settings() -> dict:
    try:
        return read_reply_settings(env_file.read()).model_dump()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Cấu hình nội dung AI đã lưu không hợp lệ") from exc


@app.post("/api/replies")
async def save_reply_settings(payload: ReplySettings) -> dict:
    env_file.update({"LLM_REPLY_SETTINGS": json.dumps(payload.model_dump(), ensure_ascii=True)})
    return {"ok": True}


def _recent_comments(path: Path, limit: int) -> list[dict]:
    if not path.is_file():
        return []
    with sqlite3.connect(path, timeout=2) as db:
        fields = {row[1] for row in db.execute("PRAGMA table_info(comments)")}
        source_col = "source" if "source" in fields else "''"
        reason_col = "reason_code" if "reason_code" in fields else "''"
        reply_col = "reply_id" if "reply_id" in fields else "NULL"
        rows = db.execute(
            f"SELECT id, author, message, status, added_at, {source_col}, {reason_col}, {reply_col} FROM comments "
            "WHERE id NOT LIKE 'sales-demo-%' ORDER BY added_at DESC, rowid DESC LIMIT ?", (limit,),
        ).fetchall()
    comments = []
    for identifier, author, message, state, added_at, stored_source, reason, reply_id in rows:
        if stored_source:
            source = stored_source
        elif identifier.startswith("tiktok:"):
            source = "tiktok"
        elif identifier.startswith("facebook-personal:"):
            source = "facebook_personal"
        elif identifier.startswith("dashboard-"):
            source = "dashboard"
        elif re.fullmatch(r"\d+(?:_\d+)?", identifier):
            source = "facebook_page"
        else:
            source = "other"
        comments.append({
            "id": identifier, "source": source, "author": author,
            "message": message, "status": state, "reason_code": reason,
            "reply_id": reply_id, "received_at": added_at,
        })
    return comments


@app.get("/api/comments/recent")
async def recent_comments() -> Response:
    values = env_file.read()
    path = Path(values.get("DATA_DIR", ROOT / "data")) / "comments.sqlite3"
    comments = await asyncio.to_thread(_recent_comments, path, 50)
    return Response(
        content=json.dumps({"comments": comments}, ensure_ascii=False),
        media_type="application/json",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/replies/recent")
async def recent_generated_replies() -> dict:
    values = env_file.read()
    path = Path(values.get("DATA_DIR", ROOT / "data")) / "comments.sqlite3"
    if not path.is_file():
        return {"replies": []}
    try:
        with sqlite3.connect(path, timeout=2) as db:
            rows = db.execute("""SELECT id, utterances_json, source_ids_json, status, reason_code, created_at
                FROM replies ORDER BY created_at DESC LIMIT 20""").fetchall()
    except sqlite3.OperationalError:
        return {"replies": []}
    catalog_path = ROOT / "src" / "fb_avatar_live" / "live_reply" / "dharma_cards.json"
    cards = {item["id"]: item for item in json.loads(catalog_path.read_text(encoding="utf-8"))} if catalog_path.is_file() else {}
    return {"replies": [{"id": row[0], "utterances": json.loads(row[1]),
                          "sources": [{"id": key, "title": cards[key]["title"], "url": cards[key]["source_url"]}
                                      for key in json.loads(row[2]) if key in cards],
                          "status": row[3], "reason_code": row[4], "created_at": row[5]}
                         for row in rows]}


def _saved_openai_key(values: dict[str, str]) -> str:
    if values.get("LLM_BASE_URL", "").rstrip("/") != OPENAI_BASE_URL:
        return ""
    return values.get("LLM_API_KEY", "")


async def _test_luna_response(api_key: str) -> None:
    from fb_avatar_live.live_reply.engine import OpenAIResponses

    schema = {"type": "object", "additionalProperties": False,
              "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
    async with httpx.AsyncClient(timeout=20) as client:
        result, moderation = await OpenAIResponses(client, api_key).structured(
            instructions="Trả lời phép thử kết nối bằng JSON đúng schema.",
            payload={"probe": "Trả ok=true"}, schema=schema,
            name="connection_probe", effort="none", max_tokens=80,
        )
    if result.get("ok") is not True or (moderation.get("output") or {}).get("type") != "moderation_result":
        raise ValueError("Luna chưa trả lời đúng định dạng hoặc thiếu moderation đầu ra")


@app.post("/api/openai/test")
async def test_openai(payload: OpenAIPayload) -> dict:
    current = env_file.read()
    api_key = payload.api_key.strip() or _saved_openai_key(current)
    if not api_key:
        raise HTTPException(status_code=400, detail="Hãy nhập OpenAI API key")
    try:
        await _test_luna_response(api_key)
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Luna chưa chạy được Responses/Structured Outputs") from exc
    return {"ok": True, "models": ["gpt-6-luna"], "model": "gpt-6-luna"}


@app.post("/api/openai/save")
async def save_openai(payload: OpenAIPayload) -> dict:
    current = env_file.read()
    api_key = payload.api_key.strip() or _saved_openai_key(current)
    if not api_key:
        raise HTTPException(status_code=400, detail="Thiếu API key")
    await test_openai(payload)
    env_file.update({"LLM_BASE_URL": OPENAI_BASE_URL, "LLM_API_KEY": api_key, "LLM_MODEL": "gpt-6-luna"})
    if _supervisor_status("fb-avatar-pipeline") == "RUNNING":
        await asyncio.to_thread(_start_or_restart, "fb-avatar-pipeline")
    return {"ok": True, "model": "gpt-6-luna"}


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
    env_file.update({
        "AVATAR_VIDEO": str(destination),
        "AVATAR_ID": info["avatar_id"],
        "AVATAR_PREP_VIDEO": "",
        "AVATAR_TIMELINE_FILE": "",
    })
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
    ssn_session_id = payload.ssn_session_id.strip() or current.get("SSN_SESSION_ID", "")
    if ssn_session_id and not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", ssn_session_id):
        raise HTTPException(status_code=400, detail="Session ID Social Stream Ninja không hợp lệ")
    existing_facebook = current.get("FACEBOOK_STREAM_URL", "")
    server = payload.facebook_server_url.strip() or current.get("FACEBOOK_SERVER_URL") or _facebook_server(existing_facebook)
    try:
        facebook_stream = payload.facebook_stream_url.strip() or _facebook_destination(server, payload.facebook_stream_key, existing_facebook)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    updates = {
        "COMMENT_SOURCE": payload.comment_source,
        "FB_OBJECT_ID": payload.fb_object_id.strip(),
        "FB_PAGE_ACCESS_TOKEN": token,
        "SSN_SESSION_ID": ssn_session_id,
        "FACEBOOK_SERVER_URL": server,
        "FACEBOOK_STREAM_URL": facebook_stream,
        "TIKTOK_UNIQUE_ID": payload.tiktok_unique_id.strip().lstrip("@"),
        "FB_POLL_SECONDS": f"{payload.poll_seconds:g}",
        "FB_BATCH_SECONDS": f"{payload.batch_seconds:g}",
        "DEMO_SALES_MODE": str(payload.demo_sales_mode).lower(),
        "STREAM_URL": "rtsp://127.0.0.1:8554/avatar",
    }
    if (_supervisor_status("fb-avatar-forwarder") == "RUNNING"
            and facebook_stream != existing_facebook):
        raise HTTPException(status_code=409, detail="Dừng gửi Facebook trước khi đổi URL hoặc khóa luồng Facebook")
    pipeline_keys = {"FB_POLL_SECONDS", "FB_BATCH_SECONDS", "DEMO_SALES_MODE", "STREAM_URL"}
    restart_pipeline = any(current.get(key, "") != updates[key] for key in pipeline_keys)
    env_file.update(updates)
    if restart_pipeline and _supervisor_status("fb-avatar-pipeline") == "RUNNING":
        await asyncio.to_thread(_start_or_restart, "fb-avatar-pipeline")
    return {"ok": True}


def _start_live() -> None:
    values = env_file.read()
    configured = _configured(values)
    source = values.get("COMMENT_SOURCE", "facebook")
    demo_sales = source == "demo" and values.get("DEMO_SALES_MODE", "false").lower() in {"1", "true", "yes", "on"}
    required = ("voice", "avatar") if demo_sales else ("openai", "voice", "avatar")
    missing = [name for name in required if not configured[name]]
    if source in {"facebook", "both"} and not configured["facebook"]:
        missing.append("facebook comments")
    if source == "facebook_personal" and not configured["facebook_personal"]:
        missing.append("Social Stream Ninja session ID")
    if source in {"tiktok", "both"} and not configured["tiktok"]:
        missing.append("tiktok comments")
    if missing:
        raise ValueError("Thiếu cấu hình: " + ", ".join(missing))
    for name in ["fb-avatar-mediamtx", "fb-avatar-musetalk", "fb-avatar-pipeline"]:
        if _supervisor_status(name) != "RUNNING":
            _start_or_restart(name)


@app.post("/api/live/start")
async def start_live() -> dict:
    try:
        await asyncio.to_thread(_start_live)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "message": "Luồng đang khởi động; Facebook chưa phát"}


@app.post("/api/live/facebook/start")
async def start_facebook() -> dict:
    if not env_file.read().get("FACEBOOK_STREAM_URL"):
        raise HTTPException(status_code=400, detail="Hãy lưu URL máy chủ và khóa luồng Facebook trước")
    if not await _path_ready():
        raise HTTPException(status_code=409, detail="Hãy khởi động luồng và chờ có tín hiệu trước")
    if _supervisor_status("fb-avatar-forwarder") != "RUNNING":
        await asyncio.to_thread(_start_or_restart, "fb-avatar-forwarder")
    return {"ok": True, "message": "Đang thử kết nối Facebook; theo dõi trạng thái rồi kiểm tra hình/tiếng trong Live Producer"}


@app.post("/api/live/facebook/stop")
async def stop_facebook() -> dict:
    await asyncio.to_thread(_stop, "fb-avatar-forwarder")
    return {"ok": True}


@app.post("/api/live/stop")
async def stop_live() -> dict:
    for name in ["fb-avatar-forwarder", "fb-avatar-pipeline", "fb-avatar-musetalk"]:
        await asyncio.to_thread(_stop, name)
    return {"ok": True}


@app.post("/api/live/test")
async def test_live(payload: TestPayload) -> dict:
    from fb_avatar_live.core import Comment, CommentStore

    message = payload.message.strip()
    if not message:
        raise HTTPException(status_code=422, detail="Nhập bình luận trước khi gửi")
    values = env_file.read()
    store = CommentStore(Path(values.get("DATA_DIR", ROOT / "data")) / "comments.sqlite3")
    try:
        identifier = f"dashboard-{uuid.uuid4().hex}"
        await asyncio.to_thread(store.add, [Comment(identifier, "Bạn (xem thử)", message)])
    finally:
        store.close()
    return {"ok": True, "message": "Đã gửi bình luận vào luồng; avatar sẽ trả lời sau các đoạn đã dựng sẵn"}


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
