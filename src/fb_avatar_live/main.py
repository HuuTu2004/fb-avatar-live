from __future__ import annotations

import asyncio
import logging

import httpx

from .config import Settings
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


async def async_main() -> None:
    settings = Settings.from_env()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    timeout = httpx.Timeout(60, connect=15)
    async with httpx.AsyncClient(timeout=timeout) as client:
        if settings.comment_source == "facebook":
            source = FacebookCommentSource(settings, client)
        elif settings.comment_source == "facebook_personal":
            source = FacebookPersonalCommentSource(settings)
        elif settings.comment_source == "tiktok":
            source = TikTokCommentSource(settings)
        elif settings.comment_source == "both":
            source = CombinedCommentSource(FacebookCommentSource(settings, client), TikTokCommentSource(settings))
        else:
            source = DemoCommentSource(settings.demo_comments_file)
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
