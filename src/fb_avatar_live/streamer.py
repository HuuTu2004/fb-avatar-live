from __future__ import annotations

import logging
import os
import queue
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Callable

from .config import Settings, read_env

log = logging.getLogger(__name__)


def write_commerce_label(data_dir: Path, enabled: bool) -> Path:
    """Atomically refresh the viewer-facing label read by the live FFmpeg process."""
    path = data_dir / "stream" / "commerce-label.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        temporary.write_text("Giỏ hàng: liên kết tiếp thị" if enabled else "", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


class SegmentStreamer:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.video_queue: queue.Queue[Path] = queue.Queue(settings.queue_size)
        self.audio_queue: queue.Queue[Path] = queue.Queue(settings.queue_size)
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.pending_lock = threading.Lock()
        self.pending_streams: dict[Path, dict[str, bool | None]] = {}
        self.job_ids: dict[Path, str] = {}
        self.on_segment_result: Callable[[str, bool | None], None] | None = None
        self.publisher_process: subprocess.Popen | None = None
        self.sales_paths: set[Path] = set()
        self.stream_dir = settings.data_dir / "stream"
        self.commerce_label = self.stream_dir / "commerce-label.txt"
        self.idle_mp4 = self.stream_dir / "idle.mp4"
        self.idle_video = b""
        self.idle_audio = b""
        self.initial_buffer_deadline = time.monotonic() + 1.0

    def enqueue(self, path: Path, is_sales: bool = False, job_id: str | None = None) -> None:
        while not self.stop_event.is_set():
            with self.pending_lock:
                if not self.video_queue.full() and not self.audio_queue.full():
                    self.pending_streams[path] = {"video": None, "audio": None}
                    self.job_ids[path] = job_id or path.stem
                    if is_sales:
                        self.sales_paths.add(path)
                    self.video_queue.put_nowait(path)
                    self.audio_queue.put_nowait(path)
                    return
            time.sleep(0.02)
        raise RuntimeError("Streamer stopped before segment could be queued")

    def drop_pending_sales(self, keep_bridge: bool = True) -> None:
        # Drop only clips still queued in both tracks; a clip already claimed by
        # either feeder must finish on both tracks to preserve lip sync.
        cancelled: list[str] = []
        with self.pending_lock, self.video_queue.mutex, self.audio_queue.mutex:
            common = self.sales_paths & set(self.video_queue.queue) & set(self.audio_queue.queue)
            # Keep one prepared clip only until the viewer reply is ready.
            bridge = next((path for path in self.video_queue.queue if path in common), None) if keep_bridge else None
            discard = common - {bridge} if bridge is not None else common
            if not discard:
                return
            for source in (self.video_queue, self.audio_queue):
                source.queue = deque(path for path in source.queue if path not in discard)
                source.unfinished_tasks -= len(discard)
                source.not_full.notify_all()
            for path in discard:
                self.pending_streams.pop(path, None)
                cancelled.append(self.job_ids.pop(path, path.stem))
                self.sales_paths.discard(path)
                path.unlink(missing_ok=True)
        if self.on_segment_result:
            for job_id in cancelled:
                self.on_segment_result(job_id, None)

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self._prepare_idle()
        self.initial_buffer_deadline = time.monotonic() + 1.0
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
        affiliate_enabled = read_env(Path(".env")).get("AFFILIATE_CART_CONFIRMED", "false").casefold() in {
            "true", "1", "yes", "on"
        }
        write_commerce_label(self.settings.data_dir, affiliate_enabled)
        idle_frame_seconds = os.getenv("AVATAR_IDLE_FRAME_SECONDS", "").strip()
        video_input = ["-stream_loop", "-1", "-i", str(self.settings.avatar_video)]
        if idle_frame_seconds:
            idle_frame = self.stream_dir / "idle-frame.png"
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-ss", idle_frame_seconds,
                 "-i", str(self.settings.avatar_video), "-frames:v", "1", str(idle_frame)],
                check=True,
            )
            video_input = ["-loop", "1", "-framerate", str(self.settings.output_fps), "-i", str(idle_frame)]
        subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error", *video_input,
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
            "-i", f"pipe:{audio_read}",
        ]
        music_file = os.getenv("BACKGROUND_MUSIC_FILE", "").strip()
        if music_file:
            if not Path(music_file).is_file():
                raise FileNotFoundError(f"BACKGROUND_MUSIC_FILE does not exist: {music_file}")
            volume = float(os.getenv("BACKGROUND_MUSIC_VOLUME", "0.10"))
            if not 0 <= volume <= 1:
                raise ValueError("BACKGROUND_MUSIC_VOLUME must be between 0 and 1")
            command += [
                "-stream_loop", "-1", "-re", "-i", music_file,
                "-filter_complex",
                f"[2:a]aresample=48000,volume={volume}[music];"
                "[1:a][music]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[mixed]",
                "-map", "0:v:0", "-map", "[mixed]",
            ]
        else:
            command += ["-map", "0:v:0", "-map", "1:a:0"]
        command += [
            "-vf", "drawtext=font='DejaVu Sans':text='Avatar AI':"
            "x=w-tw-20:y=h-th-20:fontsize=20:fontcolor=white:"
            "box=1:boxcolor=black@0.65:boxborderw=7,"
            f"drawtext=font='DejaVu Sans':textfile='{self.commerce_label.resolve()}':reload=25:"
            "x=20:y=h-th-20:fontsize=20:fontcolor=white:"
            "box=1:boxcolor=black@0.65:boxborderw=7",
            *self._encoder_args(), "-g", str(max(1, self.settings.output_fps // 5)),
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

    def _segment_done(self, path: Path, track: str, succeeded: bool) -> None:
        result: tuple[str, bool] | None = None
        with self.pending_lock:
            tracks = self.pending_streams.get(path)
            if tracks is None:
                return
            tracks[track] = succeeded
            if None in tracks.values():
                return
            self.pending_streams.pop(path, None)
            self.sales_paths.discard(path)
            publisher_alive = self.publisher_process is not None and self.publisher_process.poll() is None
            result = (self.job_ids.pop(path, path.stem), all(tracks.values()) and publisher_alive)
        path.unlink(missing_ok=True)
        if self.on_segment_result and result:
            self.on_segment_result(*result)

    def _next_segment(
        self, source_queue: queue.Queue[Path], buffering: bool
    ) -> tuple[Path | None, bool]:
        if (buffering and source_queue.qsize() < self.settings.stream_prebuffer_segments
                and (source_queue.empty() or time.monotonic() < self.initial_buffer_deadline)):
            return None, True
        try:
            return source_queue.get_nowait(), False
        except queue.Empty:
            return None, False

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
            succeeded = False
            try:
                self._decode_video(path, descriptor)
                succeeded = True
            finally:
                self._segment_done(path, "video", succeeded)
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
            succeeded = False
            try:
                self._decode_audio(path, descriptor)
                succeeded = True
            finally:
                self._segment_done(path, "audio", succeeded)
                self.audio_queue.task_done()

    def _stream_once(self) -> None:
        self.initial_buffer_deadline = time.monotonic() + 1.0
        video_read, video_write = os.pipe()
        audio_read, audio_write = os.pipe()
        process = self._publisher(video_read, audio_read)
        self.publisher_process = process
        os.close(video_read)
        os.close(audio_read)
        video = threading.Thread(target=self._feed_video, args=(video_write,), daemon=True)
        audio = threading.Thread(target=self._feed_audio, args=(audio_write,), daemon=True)
        video.start()
        audio.start()
        try:
            while process.poll() is None and not self.stop_event.is_set() and video.is_alive() and audio.is_alive():
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
            self.publisher_process = None
