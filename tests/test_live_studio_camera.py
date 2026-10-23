import unittest

try:
    from scripts.live_studio_camera import LiveFrames, MAX_AUDIO_BYTES
except (ImportError, OSError):
    LiveFrames = None


@unittest.skipIf(LiveFrames is None, "Windows virtual camera dependencies are not installed")
class LiveFramesTests(unittest.TestCase):
    def test_audio_underflow_and_overflow(self):
        frames = LiveFrames(16, 9)
        self.assertEqual(frames.take_audio(4), bytes(4))
        frames.put_audio(b"1234")
        self.assertEqual(frames.take_audio(8), b"1234" + bytes(4))
        frames.put_audio(b"x" * (MAX_AUDIO_BYTES + 4))
        self.assertEqual(len(frames.audio), MAX_AUDIO_BYTES)


if __name__ == "__main__":
    unittest.main()
