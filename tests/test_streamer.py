import unittest
from pathlib import Path
from types import SimpleNamespace

from fb_avatar_live.streamer import SegmentStreamer


class StreamerOutputTests(unittest.TestCase):
    def _streamer(self, url: str) -> SegmentStreamer:
        return SegmentStreamer(SimpleNamespace(queue_size=2, data_dir=Path("data"), stream_url=url))

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


if __name__ == "__main__":
    unittest.main()
