from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


DEFAULT_SYSTEM_PROMPT = (
    "Giọng dẫn Trạm Bình An trang nghiêm, từ tốn và từ bi nhưng dễ hiểu. Xưng tôi, gọi quý vị; nối lời giảng thành một mạch bằng ví dụ đời thường, chỉ gợi bước thực tập khi đến đúng đoạn. Không xưng thầy - con, không tự nhận là tu sĩ thật."
)
DEFAULT_TOPICS = (
    "Tứ diệu đế trong một ngày nhiều lo lắng",
    "Bát chánh đạo qua cách nói chuyện ở nhà",
    "Duyên khởi và những điều kiện của một cơn giận",
    "Vô ngã khi cái tôi bị chạm tự ái",
    "Nghiệp là tác ý và lựa chọn hôm nay",
    "Chánh niệm trong lúc đi lại và làm việc",
    "Thiền tập và trở về với hơi thở",
    "Từ bi mà vẫn giữ ranh giới an toàn",
    "Chánh ngữ khi cần nói một điều khó nghe",
    "Tình bạn và sự sẻ chia đúng lúc",
    "Trung đạo khi mình ép bản thân quá sức",
    "Hai mũi tên khi gặp chuyện đau lòng",
)


class ReplySettings(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    system_prompt: str = Field(default=DEFAULT_SYSTEM_PROMPT, min_length=1, max_length=8000)
    topics: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]] = Field(
        default_factory=lambda: list(DEFAULT_TOPICS), min_length=1, max_length=100,
    )
    viewer_address: str = Field(default="quý vị", min_length=1, max_length=40, pattern=r"^[^\r\n\x00]+$")


def read_reply_settings(values: dict[str, str], system_prompt: str = DEFAULT_SYSTEM_PROMPT) -> ReplySettings:
    if values.get("LLM_REPLY_SETTINGS"):
        return ReplySettings.model_validate_json(values["LLM_REPLY_SETTINGS"])
    return ReplySettings(system_prompt=values.get("LLM_SYSTEM_PROMPT", system_prompt))


def read_env(path: Path = Path(".env")) -> dict[str, str]:
    if not path.exists():
        return {}
    values = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def load_env(path: Path = Path(".env")) -> None:
    for key, value in read_env(path).items():
        os.environ.setdefault(key, value)


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
    ssn_session_id: str
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
            ssn_session_id=os.getenv("SSN_SESSION_ID", "").strip(),
            tiktok_unique_id=os.getenv("TIKTOK_UNIQUE_ID", "").strip().lstrip("@"),
            llm_base_url=os.getenv("LLM_BASE_URL", "").rstrip("/"),
            llm_api_key=os.getenv("LLM_API_KEY", ""),
            llm_model="gpt-6-luna" if os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/") == "https://api.openai.com/v1" else os.getenv("LLM_MODEL", ""),
            llm_max_reply_chars=_integer("LLM_MAX_REPLY_CHARS", 260),
            llm_system_prompt=os.getenv("LLM_SYSTEM_PROMPT", DEFAULT_SYSTEM_PROMPT),
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
        if self.comment_source == "facebook_personal" and not self.ssn_session_id:
            missing.append("SSN_SESSION_ID")
        if self.comment_source not in {"facebook", "facebook_personal", "tiktok", "both", "demo"}:
            raise ValueError("COMMENT_SOURCE must be facebook, facebook_personal, tiktok, both, or demo")
        if self.ssn_session_id and not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", self.ssn_session_id):
            raise ValueError("SSN_SESSION_ID is invalid")
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
