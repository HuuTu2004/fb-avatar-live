from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_env(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


def _integer(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _number(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc


def _boolean(name: str, default: bool) -> bool:
    value = os.getenv(name, str(default)).strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


@dataclass(frozen=True)
class Settings:
    comment_source: str
    fb_graph_version: str
    fb_object_id: str
    fb_page_access_token: str
    fb_fetch_limit: int
    fb_poll_seconds: float
    fb_batch_seconds: float
    fb_batch_size: int
    tiktok_unique_id: str
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    llm_max_reply_chars: int
    llm_system_prompt: str
    zerotts_model_dir: str
    zerotts_voice: str
    zerotts_threads: int
    zerotts_provider: str
    zerotts_warmup: bool
    speech_chunk_chars: int
    musetalk_url: str
    stream_url: str
    avatar_video: Path
    output_width: int
    output_height: int
    output_fps: int
    video_encoder: str
    video_bitrate: str
    idle_chunk_seconds: float
    stream_prebuffer_segments: int
    data_dir: Path
    queue_size: int
    log_level: str
    demo_comments_file: Path
    demo_sales_mode: bool

    @classmethod
    def from_env(cls) -> "Settings":
        load_env()
        settings = cls(
            comment_source=os.getenv("COMMENT_SOURCE", "facebook").lower(),
            fb_graph_version=os.getenv("FB_GRAPH_VERSION", "v26.0"),
            fb_object_id=os.getenv("FB_OBJECT_ID", ""),
            fb_page_access_token=os.getenv("FB_PAGE_ACCESS_TOKEN", ""),
            fb_fetch_limit=_integer("FB_FETCH_LIMIT", 100),
            fb_poll_seconds=_number("FB_POLL_SECONDS", 2),
            fb_batch_seconds=_number("FB_BATCH_SECONDS", 3),
            fb_batch_size=_integer("FB_BATCH_SIZE", 8),
            tiktok_unique_id=os.getenv("TIKTOK_UNIQUE_ID", "").strip().lstrip("@"),
            llm_base_url=os.getenv("LLM_BASE_URL", "").rstrip("/"),
            llm_api_key=os.getenv("LLM_API_KEY", ""),
            llm_model=os.getenv("LLM_MODEL", ""),
            llm_max_reply_chars=_integer("LLM_MAX_REPLY_CHARS", 260),
            llm_system_prompt=os.getenv(
                "LLM_SYSTEM_PROMPT",
                "Bạn là người dẫn livestream thân thiện. Trả lời tự nhiên bằng tiếng Việt, ngắn gọn và không dùng Markdown.",
            ),
            zerotts_model_dir=os.getenv("ZEROTTS_MODEL_DIR", "zeroweight-ai/ZeroTTS"),
            zerotts_voice=os.getenv("ZEROTTS_VOICE", "maichi"),
            zerotts_threads=_integer("ZEROTTS_THREADS", 4),
            zerotts_provider=os.getenv("ZEROTTS_PROVIDER", "cpu").lower(),
            zerotts_warmup=_boolean("ZEROTTS_WARMUP", True),
            speech_chunk_chars=_integer("SPEECH_CHUNK_CHARS", 260),
            musetalk_url=os.getenv("MUSETALK_URL", "http://127.0.0.1:18081").rstrip("/"),
            stream_url=os.getenv("STREAM_URL", ""),
            avatar_video=Path(os.getenv("AVATAR_VIDEO", "assets/avatar.mp4")),
            output_width=_integer("OUTPUT_WIDTH", 1280),
            output_height=_integer("OUTPUT_HEIGHT", 720),
            output_fps=_integer("OUTPUT_FPS", 25),
            video_encoder=os.getenv("VIDEO_ENCODER", "libx264"),
            video_bitrate=os.getenv("VIDEO_BITRATE", "4500k"),
            idle_chunk_seconds=_number("IDLE_CHUNK_SECONDS", 0.2),
            stream_prebuffer_segments=_integer("STREAM_PREBUFFER_SEGMENTS", 2),
            data_dir=Path(os.getenv("DATA_DIR", "data")),
            queue_size=_integer("QUEUE_SIZE", 24),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            demo_comments_file=Path(os.getenv("DEMO_COMMENTS_FILE", "data/demo_comments.jsonl")),
            demo_sales_mode=_boolean("DEMO_SALES_MODE", False),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        missing = []
        if self.comment_source in {"facebook", "both"}:
            if not self.fb_object_id:
                missing.append("FB_OBJECT_ID")
            if not self.fb_page_access_token:
                missing.append("FB_PAGE_ACCESS_TOKEN")
        if self.comment_source in {"tiktok", "both"} and not self.tiktok_unique_id:
            missing.append("TIKTOK_UNIQUE_ID")
        if self.comment_source not in {"facebook", "tiktok", "both", "demo"}:
            raise ValueError("COMMENT_SOURCE must be facebook, tiktok, both, or demo")
        if self.zerotts_provider not in {"cpu", "cuda"}:
            raise ValueError("ZEROTTS_PROVIDER must be cpu or cuda")
        required = [("STREAM_URL", self.stream_url)]
        if not (self.comment_source == "demo" and self.demo_sales_mode):
            required.extend(
                (
                    ("LLM_BASE_URL", self.llm_base_url),
                    ("LLM_API_KEY", self.llm_api_key),
                    ("LLM_MODEL", self.llm_model),
                )
            )
        for name, value in required:
            if not value:
                missing.append(name)
        if missing:
            raise ValueError("Missing required settings: " + ", ".join(missing))
        if min(
            self.output_width,
            self.output_height,
            self.output_fps,
            self.queue_size,
            self.speech_chunk_chars,
            self.stream_prebuffer_segments,
        ) <= 0:
            raise ValueError("Output dimensions, FPS, and QUEUE_SIZE must be positive")
        if not 1 <= self.fb_fetch_limit <= 100:
            raise ValueError("FB_FETCH_LIMIT must be between 1 and 100")
