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
from fb_avatar_live.core import Comment, CommentStore


class PersonalFacebookLiveTests(unittest.TestCase):
    def test_missing_supervisor_reports_missing_service_and_clear_start_error(self) -> None:
        with patch.object(self.panel.subprocess, "run", side_effect=FileNotFoundError):
            self.assertEqual(self.panel._supervisor_status("fb-avatar-pipeline"), "MISSING")
            with self.assertRaisesRegex(RuntimeError, "Supervisor"):
                self.panel._start_or_restart("fb-avatar-pipeline")

    def test_reply_settings_round_trip_preserves_multiline_text_and_other_configuration(self) -> None:
        panel = self.panel
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("LLM_SYSTEM_PROMPT=Vai trò cũ\nLLM_API_KEY=keep-secret\n", encoding="utf-8")
            with patch.object(panel, "env_file", panel.EnvFile(path)), \
                 patch.object(panel, "_start_or_restart") as restart:
                defaults = asyncio.run(panel.reply_settings())
                self.assertEqual(defaults["system_prompt"], "Vai trò cũ")
                payload = panel.ReplySettings(system_prompt='Bạn là cô Lan.\nNói "gần gũi".\u2028Dễ hiểu.',
                                              topics=[" Nấu ăn ", "Mẹo gia đình"], viewer_address=" bạn ")
                self.assertEqual(asyncio.run(panel.save_reply_settings(payload)), {"ok": True})
                self.assertEqual(asyncio.run(panel.reply_settings()), payload.model_dump())
                self.assertEqual(panel.env_file.read()["LLM_API_KEY"], "keep-secret")
                self.assertEqual(len(path.read_text(encoding="utf-8").splitlines()), 3)
                restart.assert_not_called()

    def test_reply_settings_reject_empty_or_oversized_fields(self) -> None:
        from pydantic import ValidationError

        for fields in ({"system_prompt": " "}, {"topics": []}, {"topics": [" "]},
                       {"topics": ["x" * 201]}, {"topics": ["a"] * 101},
                       {"viewer_address": " "}, {"viewer_address": "a\nb"}):
            with self.subTest(fields=fields), self.assertRaises(ValidationError):
                self.panel.ReplySettings(**fields)

    def test_affiliate_toggle_requires_explicit_cart_confirmation(self) -> None:
        panel = self.panel
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text(f"LLM_API_KEY=keep-secret\nDATA_DIR={directory}\n", encoding="utf-8")
            with patch.object(panel, "env_file", panel.EnvFile(path)):
                self.assertFalse(asyncio.run(panel.affiliate_settings())["cart_confirmed"])
                result = asyncio.run(panel.save_affiliate_settings(panel.AffiliatePayload(cart_confirmed=True)))
                self.assertTrue(result["cart_confirmed"])
                self.assertTrue(asyncio.run(panel.affiliate_settings())["cart_confirmed"])
                self.assertEqual(panel.env_file.read()["LLM_API_KEY"], "keep-secret")
                label = Path(directory) / "stream" / "commerce-label.txt"
                self.assertEqual(label.read_text(encoding="utf-8"), "Giỏ hàng: liên kết tiếp thị")
                asyncio.run(panel.save_affiliate_settings(panel.AffiliatePayload(cart_confirmed=False)))
                self.assertEqual(label.read_text(encoding="utf-8"), "")

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

    def test_tiktok_comments_can_be_configured_while_facebook_is_sending(self) -> None:
        panel = self.panel
        destination = panel.FACEBOOK_SERVER_URL + "FB-test-key"
        current = {
            "COMMENT_SOURCE": "demo", "FB_POLL_SECONDS": "2", "FB_BATCH_SECONDS": "2",
            "DEMO_SALES_MODE": "true", "STREAM_URL": "rtsp://127.0.0.1:8554/avatar",
            "FACEBOOK_SERVER_URL": panel.FACEBOOK_SERVER_URL, "FACEBOOK_STREAM_URL": destination,
        }
        payload = panel.LivePayload(comment_source="tiktok", tiktok_unique_id="@creator",
                                    demo_sales_mode=True, facebook_server_url=panel.FACEBOOK_SERVER_URL)
        states = {"fb-avatar-forwarder": "RUNNING", "fb-avatar-pipeline": "RUNNING"}
        with patch.object(panel.env_file, "read", return_value=current), \
             patch.object(panel.env_file, "update") as update, \
             patch.object(panel, "_supervisor_status", side_effect=states.get), \
             patch.object(panel, "_start_or_restart") as restart:
            asyncio.run(panel.save_live(payload))
        self.assertEqual(update.call_args.args[0]["TIKTOK_UNIQUE_ID"], "creator")
        self.assertEqual(update.call_args.args[0]["FACEBOOK_STREAM_URL"], destination)
        restart.assert_not_called()

    def test_running_facebook_destination_cannot_change(self) -> None:
        panel = self.panel
        current = {"FACEBOOK_STREAM_URL": panel.FACEBOOK_SERVER_URL + "FB-old-key"}
        payload = panel.LivePayload(facebook_server_url=panel.FACEBOOK_SERVER_URL,
                                    facebook_stream_key="FB-new-key")
        with patch.object(panel.env_file, "read", return_value=current), \
             patch.object(panel.env_file, "update") as update, \
             patch.object(panel, "_supervisor_status", return_value="RUNNING"):
            with self.assertRaises(HTTPException) as error:
                asyncio.run(panel.save_live(payload))
        self.assertEqual(error.exception.status_code, 409)
        update.assert_not_called()

    def test_recent_comments_feed_labels_sources_and_excludes_old_demo_prompts(self) -> None:
        panel = self.panel
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "comments.sqlite3"
            store = CommentStore(path)
            store.add([
                Comment("tiktok:1", "TikTok · Linh", "Xin chào"),
                Comment("facebook-personal:1", "Nam", "A di đà Phật"),
                Comment("123_456", "Hoa", "Hỏi về từ bi"),
                Comment("dashboard-1", "Bạn", "Bình luận thử"),
                Comment("sales-demo-1", "Nội bộ", "Không hiển thị"),
            ])
            store.mark_processed(["tiktok:1"])
            store.close()
            with patch.object(panel.env_file, "read", return_value={"DATA_DIR": directory}):
                response = asyncio.run(panel.recent_comments())
            payload = json.loads(response.body)
            self.assertEqual([item["source"] for item in payload["comments"]],
                             ["dashboard", "facebook_page", "facebook_personal", "tiktok"])
            self.assertEqual(payload["comments"][-1]["status"], "processed")
            self.assertEqual(response.headers["cache-control"], "no-store")

    def test_facebook_forwarder_cannot_send_to_tiktok(self) -> None:
        with patch.dict(os.environ, {"FACEBOOK_STREAM_URL": "rtmps://fb.invalid/key"}):
            self.assertEqual(_destinations("facebook"), [("Facebook", "rtmps://fb.invalid/key")])
            with self.assertRaises(ValueError):
                _destinations("tiktok")

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
