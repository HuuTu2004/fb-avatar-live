import unittest
import io
import queue
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


if __name__ == "__main__":
    unittest.main()
