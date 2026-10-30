from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StoredComment:
    id: str
    author: str
    message: str
    created_time: str = ""
    source: str = "other"


@dataclass(frozen=True)
class ReplyDecision:
    action: str
    selected_ids: tuple[str, ...] = ()
    related_ids: tuple[str, ...] = ()
    deferred_ids: tuple[str, ...] = ()
    ignored_ids: tuple[str, ...] = ()
    reason_code: str = ""
    utterances: tuple[str, ...] = ()
    source_ids: tuple[str, ...] = ()
    withhold_reason: str | None = None
    prompt_version: str = "dharma-v1"
    model_id: str = "gpt-6-luna"


@dataclass(frozen=True)
class SpeechJob:
    id: str
    reply_id: str
    index: int
    text: str
    is_background: bool
