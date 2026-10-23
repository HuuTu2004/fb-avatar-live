from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fb_avatar_live.config import Settings
from fb_avatar_live.main import ReloadingCommentSource


class CommentReloadTests(unittest.TestCase):
    def test_switches_to_tiktok_without_restarting_the_pipeline(self) -> None:
        class Reader:
            def __init__(self, name):
                self.name = name
                self.closed = False

            async def fetch(self):
                return [self.name]

            async def aclose(self):
                self.closed = True

        async def check(path, settings):
            readers = []

            def make_source(updated, _client):
                reader = Reader((updated.comment_source, updated.tiktok_unique_id))
                readers.append(reader)
                return reader

            with patch("fb_avatar_live.main.make_comment_source", side_effect=make_source):
                source = ReloadingCommentSource(settings, object(), path)
                self.assertEqual(await source.fetch(), [("demo", "")])
                path.write_text("COMMENT_SOURCE=tiktok\nTIKTOK_UNIQUE_ID=@bom_36a\n", encoding="utf-8")
                self.assertEqual(await source.fetch(), [("tiktok", "bom_36a")])
                self.assertTrue(readers[0].closed)
                self.assertFalse(readers[1].closed)
                await source.aclose()
                self.assertTrue(readers[1].closed)

        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"COMMENT_SOURCE": "demo", "DEMO_SALES_MODE": "true", "STREAM_URL": "rtsp://example/avatar"}
        ):
            path = Path(directory) / ".env"
            path.write_text("COMMENT_SOURCE=demo\n", encoding="utf-8")
            asyncio.run(check(path, Settings.from_env()))


if __name__ == "__main__":
    unittest.main()
