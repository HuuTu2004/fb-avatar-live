from __future__ import annotations

import asyncio
from contextlib import suppress
import logging
import os
import time
from pathlib import Path

from fb_avatar_live.config import read_env
from fb_avatar_live.core import AudioJob, SalesDemoLLM, TextJob

from .engine import LiveReplyEngine, PROMPT_VERSION
from .program import AFFILIATE_MENTION, INVITATIONS, OPENING
from .state_store import LiveStateStore
from .types import ReplyDecision

log = logging.getLogger(__name__)


class LiveReplyCoordinator:
    """Connects durable decisions to the existing speech and video pipeline."""

    def __init__(self, settings, source, engine: LiveReplyEngine | None, tts, renderer, streamer):
        self.settings = settings
        self.source = source
        self.engine = engine
        self.tts = tts
        self.renderer = renderer
        self.streamer = streamer
        self.store = LiveStateStore(settings.data_dir / "comments.sqlite3")
        self.text_queue: asyncio.Queue[TextJob] = asyncio.Queue(settings.queue_size)
        self.audio_queue: asyncio.Queue[AudioJob] = asyncio.Queue(settings.queue_size)
        self.synthesizing = 0
        self.rendering = 0
        self.background_generation = 0
        self.offline_narrator = SalesDemoLLM()
        now = time.monotonic()
        self.last_viewer_at = now
        self.next_invitation_at = now + 180
        self.next_affiliate_at = now + 300
        self.invitation_index = 0

    def _pause_speech(self) -> bool:
        value = read_env(Path(".env")).get("PAUSE_AI_SPEECH", os.getenv("PAUSE_AI_SPEECH", "false"))
        return value.casefold() in {"true", "1", "yes", "on"}

    def _affiliate_cart_confirmed(self) -> bool:
        value = read_env(Path(".env")).get("AFFILIATE_CART_CONFIRMED",
                                           os.getenv("AFFILIATE_CART_CONFIRMED", "false"))
        return value.casefold() in {"true", "1", "yes", "on"}

    @staticmethod
    def _script_decision(text: str, reason: str) -> ReplyDecision:
        return ReplyDecision("speak", reason_code=reason, utterances=(text,),
                             prompt_version=PROMPT_VERSION, model_id="local-script")

    def _discard_background(self) -> None:
        for queue in (self.text_queue, self.audio_queue):
            retained = []
            while not queue.empty():
                job = queue.get_nowait()
                queue.task_done()
                if job.is_sales:
                    self.store.record_media_event(job.id, None)
                    if isinstance(job, AudioJob):
                        job.path.unlink(missing_ok=True)
                else:
                    retained.append(job)
            for job in retained:
                queue.put_nowait(job)

    def _media_result(self, job_id: str, success: bool | None) -> None:
        try:
            self.store.record_media_event(job_id, success)
        except Exception:
            log.exception("Could not record media result for %s", job_id)

    async def poll_comments(self) -> None:
        while True:
            await self._poll_once()
            await asyncio.sleep(self.settings.fb_poll_seconds)

    async def _poll_once(self) -> None:
        try:
            added = await asyncio.to_thread(self.store.record, await self.source.fetch())
            if added:
                self.last_viewer_at = time.monotonic()
                log.info("Stored %d new comments", added)
                # Keep prepared narration while a viewer answer is planned.
        except Exception:
            log.exception("Comment poll failed")

    async def create_replies(self) -> None:
        while True:
            await self._create_reply_once()
            await asyncio.sleep(self.settings.fb_batch_seconds)

    async def _create_reply_once(self) -> None:
        if self._pause_speech() or self.engine is None:
            return
        window = await asyncio.to_thread(self.store.claim_window, self.settings.fb_batch_size)
        if not window:
            return
        try:
            decision = await self.engine.plan(window, self.store.recent_replies())
            reply_id = await asyncio.to_thread(self.store.commit, decision, window)
            if reply_id:
                log.info("Queued reply %s for %s: %s", reply_id, [c.id for c in window], decision.reason_code)
        except Exception:
            log.exception("Reply planning failed")
            await asyncio.to_thread(self.store.release_window, [c.id for c in window])

    async def maintain_narration(self) -> None:
        while True:
            try:
                await self._create_background_once()
            except Exception:
                log.exception("Could not prepare continuity narration")
            await asyncio.sleep(0.5)

    async def _fallback_narration(self) -> ReplyDecision:
        if isinstance(self.engine, LiveReplyEngine):
            fallback = self.engine.fallback_narration()
            if fallback:
                text, source_ids = fallback
                return ReplyDecision("speak", reason_code="continuity_fallback", utterances=(text,),
                                     source_ids=source_ids, prompt_version=PROMPT_VERSION, model_id="local-script")
        text = await self.offline_narrator.reply([])
        return self._script_decision(text, "continuity_fallback")

    async def _create_background_once(self) -> None:
        if not self.settings.demo_sales_mode or self._pause_speech() or self.store.viewer_jobs_waiting():
            return
        if self.store.background_backlog() >= max(3, self.settings.stream_prebuffer_segments + 2):
            return
        generation = self.background_generation
        now = time.monotonic()
        interlude = ""
        try:
            if now >= self.next_invitation_at:
                decision = self._script_decision(INVITATIONS[self.invitation_index % len(INVITATIONS)],
                                                 "invitation_to_share")
                interlude = "invitation"
            elif (now >= self.next_affiliate_at and self._affiliate_cart_confirmed()
                  and now - self.last_viewer_at >= 180):
                decision = self._script_decision(AFFILIATE_MENTION, "affiliate_disclosed")
                interlude = "affiliate"
            elif self.engine:
                decision = await asyncio.wait_for(self.engine.narrate(self.store.recent_replies()), timeout=8)
                if not decision.utterances:
                    decision = await self._fallback_narration()
            else:
                decision = await self._fallback_narration()
        except Exception:
            log.exception("Luna narration unavailable; using local continuity script")
            decision = await self._fallback_narration()
        if generation != self.background_generation or self._pause_speech() or self.store.viewer_jobs_waiting():
            return
        if interlude == "affiliate" and time.monotonic() - self.last_viewer_at < 180:
            return
        reply_id = await asyncio.to_thread(self.store.commit, decision, [])
        if (reply_id and decision.reason_code == "background_dharma"
                and isinstance(self.engine, LiveReplyEngine)):
            self.engine.note_narration_queued(reply_id, decision.utterances[0])
        if interlude == "invitation":
            self.invitation_index += 1
            self.next_invitation_at = time.monotonic() + 180
        elif interlude == "affiliate":
            self.next_affiliate_at = time.monotonic() + 900
        log.info("Prepared narration %s: %s", reply_id, decision.reason_code)

    async def _viewer_segment_ready(self) -> None:
        self.background_generation += 1
        cancelled = self.store.cancel_pending_background()
        if isinstance(self.engine, LiveReplyEngine):
            self.engine.rewind_cancelled_narration(cancelled)
        self._discard_background()
        await asyncio.to_thread(self.streamer.drop_pending_sales, False)

    async def dispatch_speech(self) -> None:
        while True:
            if self.text_queue.qsize() >= max(1, self.settings.queue_size // 2) or self._pause_speech():
                await asyncio.sleep(0.1)
                continue
            job = await asyncio.to_thread(self.store.claim_speech_job)
            if job is None:
                await asyncio.sleep(0.1)
                continue
            if job.is_background and self.store.job_status(job.id) == "cancelled":
                continue
            await self.text_queue.put(TextJob(job.id, job.text, job.is_background, self.background_generation))

    async def synthesize_audio(self) -> None:
        import soundfile as sf

        output_dir = self.settings.data_dir / "audio"
        while True:
            job = await self.text_queue.get()
            self.synthesizing += 1
            try:
                if self.store.job_status(job.id) == "cancelled":
                    continue
                started = time.perf_counter()
                audio = await asyncio.to_thread(self.tts.synthesize, job, output_dir)
                log.info("ZeroTTS synthesized %.2fs in %.2fs", sf.info(audio.path).duration,
                         time.perf_counter() - started)
                if job.is_sales and job.generation != self.background_generation:
                    audio.path.unlink(missing_ok=True)
                    self.store.record_media_event(job.id, None)
                    continue
                await self.audio_queue.put(audio)
            except Exception:
                log.exception("TTS failed for %s", job.id)
                self.store.record_media_event(job.id, False)
            finally:
                self.synthesizing -= 1
                self.text_queue.task_done()

    async def render_video(self) -> None:
        while True:
            job = await self.audio_queue.get()
            self.rendering += 1
            try:
                if self.store.job_status(job.id) == "cancelled":
                    continue
                video = await self.renderer.render(job)
                if job.is_sales and job.generation != self.background_generation:
                    video.unlink(missing_ok=True)
                    self.store.record_media_event(job.id, None)
                    continue
                if not job.is_sales:
                    await self._viewer_segment_ready()
                self.store.mark_feeding(job.id)
                await asyncio.to_thread(self.streamer.enqueue, video, is_sales=job.is_sales, job_id=job.id)
                log.info("Queued rendered segment %s", video.name)
            except Exception:
                log.exception("Render or stream queue failed for %s", job.id)
                self.store.record_media_event(job.id, False)
            finally:
                self.rendering -= 1
                job.path.unlink(missing_ok=True)
                self.audio_queue.task_done()

    async def run(self) -> None:
        await self.renderer.wait_ready()
        if self.store.background_backlog() == 0:
            await asyncio.to_thread(self.store.commit, self._script_decision(OPENING, "opening"), [])
        self.streamer.on_segment_result = self._media_result
        self.streamer.start()
        try:
            await asyncio.gather(self.poll_comments(), self.create_replies(), self.maintain_narration(), self.dispatch_speech(),
                                 self.synthesize_audio(), self.render_video())
        finally:
            if hasattr(self.source, "aclose"):
                with suppress(Exception):
                    await self.source.aclose()
            await asyncio.to_thread(self.streamer.stop)
            await asyncio.to_thread(self.store.close)
