from __future__ import annotations

import asyncio
import logging

import httpx

from .config import Settings
from .core import DemoCommentSource, ExternalLLM, FacebookCommentSource, MuseTalkClient, Pipeline, ZeroTTSEngine
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
        source = (
            FacebookCommentSource(settings, client)
            if settings.comment_source == "facebook"
            else DemoCommentSource(settings.demo_comments_file)
        )
        pipeline = Pipeline(
            settings=settings,
            source=source,
            llm=ExternalLLM(settings, client),
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
