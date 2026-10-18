from __future__ import annotations

import logging
import os
import queue
import subprocess
import threading
import time
from pathlib import Path

from .config import Settings

log = logging.getLogger(__name__)


class SegmentStreamer:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.video_queue: queue.Queue[Path] = queue.Queue(settings.queue_size)
        self.audio_queue: queue.Queue[Path] = queue.Queue(settings.queue_size)
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.pending_lock = threading.Lock()
        self.pending_streams: dict[Path, int] = {}
        self.stream_dir = settings.data_dir / "stream"
        self.idle_mp4 = self.stream_dir / "idle.mp4"
        self.idle_video = b""
        self.idle_audio = b""

    def enqueue(self, path: Path) -> None:
        with self.pending_lock:
            self.pending_streams[path] = 2
        self.video_queue.put(path)
        self.audio_queue.put(path)

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self._prepare_idle()
        self.thread = threading.Thread(target=self._supervise, name="rtmps-streamer", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=20)

    def _encoder_args(self) -> list[str]:
        if self.settings.video_encoder == "h264_nvenc":
            return ["-c:v", "h264_nvenc", "-preset", "p1", "-tune", "ll", "-b:v", self.settings.video_bitrate]
        return ["-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency", "-b:v", self.settings.video_bitrate]

    def _video_filter(self) -> str:
        return (
            f"scale={self.settings.output_width}:{self.settings.output_height}:force_original_aspect_ratio=decrease,"
            f"pad={self.settings.output_width}:{self.settings.output_height}:(ow-iw)/2:(oh-ih)/2,"
            f"fps={self.settings.output_fps},format=bgr24"
        )

    def _prepare_idle(self) -> None:
        if not self.settings.avatar_video.exists():
            raise FileNotFoundError(f"AVATAR_VIDEO does not exist: {self.settings.avatar_video}")
        self.stream_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error", "-stream_loop", "-1", "-i", str(self.settings.avatar_video),
                "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", "1",
                "-map", "0:v:0", "-map", "1:a:0", "-vf", self._video_filter().replace("format=bgr24", "format=yuv420p"),
                *self._encoder_args(), "-g", str(max(1, self.settings.output_fps // 5)), "-c:a", "aac", "-ar", "48000",
                "-ac", "2", "-shortest", "-movflags", "+faststart", str(self.idle_mp4),
            ],
            check=True,
        )
        self.idle_video = subprocess.run(
            [
                "ffmpeg", "-loglevel", "error", "-i", str(self.idle_mp4), "-map", "0:v:0", "-vf",
                self._video_filter(), "-pix_fmt", "bgr24", "-f", "rawvideo", "pipe:1",
            ],
            check=True,
            capture_output=True,
        ).stdout
        self.idle_audio = subprocess.run(
            [
                "ffmpeg", "-loglevel", "error", "-i", str(self.idle_mp4), "-map", "0:a:0", "-f", "s16le",
                "-ar", "48000", "-ac", "2", "pipe:1",
            ],
            check=True,
            capture_output=True,
        ).stdout
        if not self.idle_video or not self.idle_audio:
            raise RuntimeError("Idle clip could not be decoded")

    def _supervise(self) -> None:
        while not self.stop_event.is_set():
            try:
                self._stream_once()
            except Exception:
                if not self.stop_event.is_set():
                    log.exception("Stream process stopped; retrying")
            self.stop_event.wait(2)

    def _publisher(self, video_read: int, audio_read: int) -> subprocess.Popen:
        command = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-thread_queue_size", "512", "-re", "-f", "rawvideo", "-pix_fmt", "bgr24",
            "-s", f"{self.settings.output_width}x{self.settings.output_height}",
            "-r", str(self.settings.output_fps), "-i", f"pipe:{video_read}",
            "-thread_queue_size", "512", "-re", "-f", "s16le", "-ar", "48000", "-ac", "2",
            "-i", f"pipe:{audio_read}", *self._encoder_args(), "-g", str(max(1, self.settings.output_fps // 5)),
            "-pix_fmt", "yuv420p", *self._output_args(),
        ]
        return subprocess.Popen(command, pass_fds=(video_read, audio_read))

    def _output_args(self) -> list[str]:
        if self.settings.stream_url.startswith("rtsp://"):
            return [
                "-c:a", "libopus", "-b:a", "128k", "-ar", "48000", "-ac", "2",
                "-rtsp_transport", "tcp", "-f", "rtsp", self.settings.stream_url,
            ]
        return [
            "-c:a", "aac", "-ar", "48000", "-ac", "2", "-flvflags",
            "no_duration_filesize", "-f", "flv", self.settings.stream_url,
        ]

    def _write_all(self, descriptor: int, data: bytes) -> None:
        view = memoryview(data)
        while view and not self.stop_event.is_set():
            written = os.write(descriptor, view[: 64 * 1024])
            view = view[written:]

    def _write_looped(self, descriptor: int, data: bytes, offset: int, size: int) -> int:
        remaining = size
        while remaining and not self.stop_event.is_set():
            count = min(remaining, len(data) - offset)
            self._write_all(descriptor, data[offset:offset + count])
            offset = (offset + count) % len(data)
            remaining -= count
        return offset

    def _segment_done(self, path: Path) -> None:
        with self.pending_lock:
            remaining = self.pending_streams.get(path, 1) - 1
            if remaining:
                self.pending_streams[path] = remaining
                return
            self.pending_streams.pop(path, None)
        path.unlink(missing_ok=True)

    def _next_segment(
        self, source_queue: queue.Queue[Path], buffering: bool
    ) -> tuple[Path | None, bool]:
        if buffering and source_queue.qsize() < self.settings.stream_prebuffer_segments:
            return None, True
        try:
            return source_queue.get_nowait(), False
        except queue.Empty:
            return None, True

    def _decode_video(self, path: Path, descriptor: int) -> None:
        process = subprocess.Popen(
            [
                "ffmpeg", "-loglevel", "error", "-i", str(path), "-map", "0:v:0",
                "-vf", self._video_filter(), "-pix_fmt", "bgr24", "-f", "rawvideo",
                "pipe:1",
            ],
            stdout=subprocess.PIPE,
        )
        frame_size = self.settings.output_width * self.settings.output_height * 3
        last_frame = None
        assert process.stdout is not None
        while not self.stop_event.is_set():
            frame = process.stdout.read(frame_size)
            if not frame:
                break
            if len(frame) != frame_size:
                process.kill()
                raise RuntimeError(f"Incomplete video frame in segment: {path}")
            self._write_all(descriptor, frame)
            last_frame = frame
        if self.stop_event.is_set():
            process.terminate()
        if process.wait() and not self.stop_event.is_set():
            raise RuntimeError(f"Could not decode video segment: {path}")
        if last_frame is not None:
            self.idle_video = last_frame

    def _decode_audio(self, path: Path, descriptor: int) -> None:
        result = subprocess.run(
            [
                "ffmpeg", "-loglevel", "error", "-i", str(path), "-map", "0:a:0",
                "-af", "aresample=async=1:first_pts=0", "-f", "s16le", "-ar", "48000",
                "-ac", "2", f"pipe:{descriptor}",
            ],
            pass_fds=(descriptor,),
        )
        if result.returncode and not self.stop_event.is_set():
            raise RuntimeError(f"Could not decode audio segment: {path}")

    def _feed_video(self, descriptor: int) -> None:
        idle_offset = 0
        buffering = True
        idle_size = max(1, round(self.settings.output_fps * self.settings.idle_chunk_seconds)) * self.settings.output_width * self.settings.output_height * 3
        while not self.stop_event.is_set():
            path, buffering = self._next_segment(self.video_queue, buffering)
            if path is None:
                idle_offset = self._write_looped(descriptor, self.idle_video, idle_offset, idle_size)
                continue
            try:
                self._decode_video(path, descriptor)
            finally:
                self._segment_done(path)
                self.video_queue.task_done()

    def _feed_audio(self, descriptor: int) -> None:
        idle_offset = 0
        buffering = True
        idle_size = max(1, round(48000 * self.settings.idle_chunk_seconds)) * 2 * 2
        while not self.stop_event.is_set():
            path, buffering = self._next_segment(self.audio_queue, buffering)
            if path is None:
                idle_offset = self._write_looped(descriptor, self.idle_audio, idle_offset, idle_size)
                continue
            try:
                self._decode_audio(path, descriptor)
            finally:
                self._segment_done(path)
                self.audio_queue.task_done()

    def _stream_once(self) -> None:
        video_read, video_write = os.pipe()
        audio_read, audio_write = os.pipe()
        process = self._publisher(video_read, audio_read)
        os.close(video_read)
        os.close(audio_read)
        video = threading.Thread(target=self._feed_video, args=(video_write,), daemon=True)
        audio = threading.Thread(target=self._feed_audio, args=(audio_write,), daemon=True)
        video.start()
        audio.start()
        try:
            while process.poll() is None and not self.stop_event.is_set():
                time.sleep(0.1)
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
            video.join(timeout=5)
            audio.join(timeout=5)
            os.close(video_write)
            os.close(audio_write)
