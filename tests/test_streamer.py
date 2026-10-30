import unittest
import io
import queue
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fb_avatar_live.streamer import SegmentStreamer


class StreamerOutputTests(unittest.TestCase):
    def _streamer(self, url: str) -> SegmentStreamer:
        return SegmentStreamer(SimpleNamespace(
            queue_size=3,
            data_dir=Path("data"),
            stream_url=url,
            stream_prebuffer_segments=2,
        ))

    def test_rtsp_uses_browser_compatible_opus(self):
        args = self._streamer("rtsp://127.0.0.1:8554/avatar")._output_args()
        self.assertIn("libopus", args)
        self.assertIn("rtsp", args)

    def test_rtmps_uses_facebook_compatible_aac(self):
        args = self._streamer("rtmps://example.invalid/live/key")._output_args()
        self.assertIn("aac", args)
        self.assertIn("flv", args)

    def test_idle_writer_continues_across_loop_boundary(self):
        streamer = self._streamer("rtsp://127.0.0.1:8554/avatar")
        writes = []
        streamer._write_all = lambda _, data: writes.append(data)
        offset = streamer._write_looped(1, b"abcd", 3, 6)
        self.assertEqual(b"".join(writes), b"dabcda")
        self.assertEqual(offset, 1)

    def test_video_segment_becomes_hold_frame_for_render_gap(self):
        streamer = self._streamer("rtsp://127.0.0.1:8554/avatar")
        streamer.settings.output_width = 2
        streamer.settings.output_height = 1
        streamer.settings.output_fps = 25
        streamer._write_all = lambda *_: None

        class Process:
            stdout = io.BytesIO(b"a" * 6 + b"b" * 6)

            def wait(self):
                return 0

        with patch("fb_avatar_live.streamer.subprocess.Popen", return_value=Process()):
            streamer._decode_video(Path("segment.mp4"), 1)
        self.assertEqual(streamer.idle_video, b"b" * 6)

    def test_segments_wait_for_initial_prebuffer_then_resume_after_underrun(self):
        streamer = self._streamer("rtsp://127.0.0.1:8554/avatar")
        segments: queue.Queue[Path] = queue.Queue()
        segments.put(Path("one.mp4"))

        path, buffering = streamer._next_segment(segments, True)
        self.assertIsNone(path)
        self.assertTrue(buffering)

        segments.put(Path("two.mp4"))
        path, buffering = streamer._next_segment(segments, buffering)
        self.assertEqual(path, Path("one.mp4"))
        self.assertFalse(buffering)

        self.assertEqual(streamer._next_segment(segments, buffering)[0], Path("two.mp4"))
        self.assertEqual(streamer._next_segment(segments, buffering), (None, False))
        segments.put(Path("three.mp4"))
        self.assertEqual(streamer._next_segment(segments, False), (Path("three.mp4"), False))

    def test_single_viewer_segment_starts_after_short_prebuffer_deadline(self):
        streamer = self._streamer("rtsp://127.0.0.1:8554/avatar")
        segments: queue.Queue[Path] = queue.Queue()
        segments.put(Path("viewer.mp4"))
        streamer.initial_buffer_deadline = 0
        self.assertEqual(streamer._next_segment(segments, True), (Path("viewer.mp4"), False))

    def test_viewer_comment_discards_only_sales_clips_waiting_on_both_tracks(self):
        streamer = self._streamer("rtsp://127.0.0.1:8554/avatar")
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory) / f"{name}.mp4" for name in ("playing", "old-one", "old-two")]
            for path in paths:
                path.write_bytes(b"clip")
                streamer.enqueue(path, is_sales=True)
            self.assertEqual(streamer.audio_queue.get_nowait(), paths[0])
            streamer.drop_pending_sales()
            self.assertEqual(list(streamer.video_queue.queue), [paths[0], paths[1]])
            self.assertEqual(list(streamer.audio_queue.queue), [paths[1]])
            self.assertTrue(paths[0].exists())
            self.assertTrue(paths[1].exists())
            self.assertFalse(paths[2].exists())
            streamer.drop_pending_sales(keep_bridge=False)
            self.assertEqual(list(streamer.video_queue.queue), [paths[0]])
            self.assertEqual(list(streamer.audio_queue.queue), [])
            self.assertFalse(paths[1].exists())

    def test_full_queues_accept_viewer_clip_after_sales_are_dropped(self):
        streamer = self._streamer("rtsp://127.0.0.1:8554/avatar")
        with tempfile.TemporaryDirectory() as directory:
            sales = [Path(directory) / f"sales-{index}.mp4" for index in range(3)]
            for path in sales:
                path.write_bytes(b"clip")
                streamer.enqueue(path, is_sales=True)
            viewer = Path(directory) / "viewer.mp4"
            viewer.write_bytes(b"clip")
            writer = threading.Thread(target=streamer.enqueue, args=(viewer,))
            writer.start()
            streamer.drop_pending_sales(keep_bridge=False)
            writer.join(timeout=1)
            self.assertFalse(writer.is_alive())
            self.assertEqual(list(streamer.video_queue.queue), [viewer])
            self.assertEqual(list(streamer.audio_queue.queue), [viewer])


if __name__ == "__main__":
    unittest.main()
