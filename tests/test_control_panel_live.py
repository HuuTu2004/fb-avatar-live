from __future__ import annotations

import asyncio
import importlib
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from fb_avatar_live.forwarder import _destinations


class PersonalFacebookLiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.directory = tempfile.TemporaryDirectory()
        root = Path(cls.directory.name)
        (root / "web" / "static").mkdir(parents=True)
        (root / ".env.example").write_text("COMMENT_SOURCE=demo\n", encoding="utf-8")
        with patch.dict(os.environ, {"ROOT": str(root)}):
            cls.panel = importlib.import_module("services.control_panel")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.directory.cleanup()

    def test_server_and_key_are_saved_as_one_rtmps_destination(self) -> None:
        panel = self.panel
        destination = panel._facebook_destination(panel.FACEBOOK_SERVER_URL, "FB-test-key", "")
        self.assertEqual(destination, panel.FACEBOOK_SERVER_URL + "FB-test-key")
        self.assertEqual(panel._facebook_server(destination), panel.FACEBOOK_SERVER_URL)
        self.assertEqual(panel._facebook_destination(panel.FACEBOOK_SERVER_URL, "", destination), destination)
        with self.assertRaisesRegex(ValueError, "RTMPS"):
            panel._facebook_destination("http://example.com/rtmp/", "FB-test-key", "")

    def test_start_preview_does_not_start_any_forwarder(self) -> None:
        panel = self.panel
        values = {"COMMENT_SOURCE": "demo", "DEMO_SALES_MODE": "true"}
        with patch.object(panel.env_file, "read", return_value=values), \
             patch.object(panel, "_configured", return_value={"voice": True, "avatar": True}), \
             patch.object(panel, "_supervisor_status", return_value="STOPPED"), \
             patch.object(panel, "_start_or_restart") as start:
            panel._start_live()
        self.assertEqual([call.args[0] for call in start.call_args_list], [
            "fb-avatar-mediamtx", "fb-avatar-musetalk", "fb-avatar-pipeline"
        ])

    def test_facebook_requires_preview_before_sending(self) -> None:
        panel = self.panel
        with patch.object(panel.env_file, "read", return_value={"FACEBOOK_STREAM_URL": "rtmps://example.com/rtmp/FB-test"}), \
             patch.object(panel, "_path_ready", new=AsyncMock(return_value=False)), \
             patch.object(panel, "_start_or_restart") as start:
            with self.assertRaises(HTTPException) as error:
                asyncio.run(panel.start_facebook())
        self.assertEqual(error.exception.status_code, 409)
        start.assert_not_called()

    def test_saving_personal_stream_key_keeps_preview_running(self) -> None:
        panel = self.panel
        current = {
            "COMMENT_SOURCE": "demo", "FB_POLL_SECONDS": "2", "FB_BATCH_SECONDS": "2",
            "DEMO_SALES_MODE": "true", "STREAM_URL": "rtsp://127.0.0.1:8554/avatar",
        }
        payload = panel.LivePayload(comment_source="demo", demo_sales_mode=True,
                                    facebook_server_url=panel.FACEBOOK_SERVER_URL,
                                    facebook_stream_key="FB-test-key")
        with patch.object(panel.env_file, "read", return_value=current), \
             patch.object(panel.env_file, "update") as update, \
             patch.object(panel, "_supervisor_status", return_value="STOPPED"), \
             patch.object(panel, "_start_or_restart") as start:
            asyncio.run(panel.save_live(payload))
        self.assertEqual(update.call_args.args[0]["FACEBOOK_STREAM_URL"], panel.FACEBOOK_SERVER_URL + "FB-test-key")
        start.assert_not_called()

    def test_facebook_forwarder_cannot_send_to_tiktok(self) -> None:
        with patch.dict(os.environ, {"FACEBOOK_STREAM_URL": "rtmps://fb.invalid/key", "TIKTOK_STREAM_URL": "rtmp://tt.invalid/key"}):
            self.assertEqual(_destinations("facebook"), [("Facebook", "rtmps://fb.invalid/key")])
            self.assertEqual(_destinations("tiktok"), [("TikTok", "rtmp://tt.invalid/key")])

    def test_retrying_forwarder_is_not_reported_as_sending(self) -> None:
        panel = self.panel
        with tempfile.TemporaryDirectory() as directory, patch.object(panel, "FORWARDER_HEALTH_DIR", Path(directory)):
            (Path(directory) / "fb-avatar-forwarder-facebook.json").write_text(
                json.dumps({"state": "retrying", "updated_at": time.time(), "exit_code": 251}),
                encoding="utf-8",
            )
            self.assertEqual(panel._forwarder_health("facebook", "RUNNING"), "retrying")
            self.assertEqual(panel._forwarder_health("facebook", "STOPPED"), "stopped")


if __name__ == "__main__":
    unittest.main()
