from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from pathlib import Path

import httpx

from .config import Settings, read_env
from .core import (
    CombinedCommentSource,
    DemoCommentSource,
    ExternalLLM,
    FacebookCommentSource,
    FacebookPersonalCommentSource,
    MuseTalkClient,
    Pipeline,
    SalesDemoLLM,
    TikTokCommentSource,
    ZeroTTSEngine,
)
from .streamer import SegmentStreamer


def make_comment_source(settings: Settings, client: httpx.AsyncClient):
    if settings.comment_source == "facebook":
        return FacebookCommentSource(settings, client)
    if settings.comment_source == "facebook_personal":
        return FacebookPersonalCommentSource(settings)
    if settings.comment_source == "tiktok":
        return TikTokCommentSource(settings)
    if settings.comment_source == "both":
        return CombinedCommentSource(FacebookCommentSource(settings, client), TikTokCommentSource(settings))
    return DemoCommentSource(settings.demo_comments_file)


class ReloadingCommentSource:
    """Switch comment readers after a dashboard save without restarting the video pipeline."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient, path: Path = Path(".env")) -> None:
        self.settings = settings
        self.client = client
        self.path = path
        self.key = None
        self.source = None

    async def fetch(self):
        values = read_env(self.path)
        key = (
            values.get("COMMENT_SOURCE", self.settings.comment_source),
            values.get("FB_OBJECT_ID", self.settings.fb_object_id),
            values.get("FB_PAGE_ACCESS_TOKEN", self.settings.fb_page_access_token),
            values.get("SSN_SESSION_ID", self.settings.ssn_session_id),
            values.get("TIKTOK_UNIQUE_ID", self.settings.tiktok_unique_id).lstrip("@"),
        )
        if key != self.key:
            next_settings = replace(self.settings, comment_source=key[0], fb_object_id=key[1],
                                    fb_page_access_token=key[2], ssn_session_id=key[3], tiktok_unique_id=key[4])
            old_source = self.source
            self.source = make_comment_source(next_settings, self.client)
            self.key = key
            if old_source and hasattr(old_source, "aclose"):
                await asyncio.wait_for(old_source.aclose(), timeout=5)
            logging.getLogger(__name__).info("Comment source switched to %s", key[0])
        return await self.source.fetch()

    async def aclose(self) -> None:
        if self.source and hasattr(self.source, "aclose"):
            await self.source.aclose()


async def async_main() -> None:
    settings = Settings.from_env()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    timeout = httpx.Timeout(60, connect=15)
    async with httpx.AsyncClient(timeout=timeout) as client:
        source = ReloadingCommentSource(settings, client)
        llm = (
            ExternalLLM(settings, client)
            if settings.llm_api_key and settings.llm_model
            else SalesDemoLLM()
        )
        pipeline = Pipeline(
            settings=settings,
            source=source,
            llm=llm,
            tts=ZeroTTSEngine(settings),
            renderer=MuseTalkClient(settings, client),
            streamer=SegmentStreamer(settings),
        )
        await pipeline.run()


def run() -> None:
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
