from __future__ import annotations

import asyncio
import json
import queue
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

import httpx

from fb_avatar_live.config import ReplySettings
from fb_avatar_live.live_reply.engine import LiveReplyEngine, OpenAIResponses, _unsupported_commerce_claim
from fb_avatar_live.live_reply.coordinator import LiveReplyCoordinator
from fb_avatar_live.live_reply.knowledge import DharmaCatalog
from fb_avatar_live.live_reply.program import AFFILIATE_MENTION, OPENING
from fb_avatar_live.live_reply.state_store import LiveStateStore
from fb_avatar_live.live_reply.types import ReplyDecision, StoredComment
from fb_avatar_live.streamer import SegmentStreamer


CATALOG = Path(__file__).resolve().parents[1] / "src" / "fb_avatar_live" / "live_reply" / "dharma_cards.json"


class FakeAPI:
    def __init__(self):
        self.calls = []

    async def moderate(self, texts):
        return [{"categories": {}, "flagged": False} for _ in texts]

    async def structured(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs["name"] == "live_comment_selection":
            return ({"selected_id": "one", "related_ids": ["two"], "ignored_ids": [],
                     "topic_query": "giận và phản ứng", "reason_code": "practical_question"}, {})
        return ({"utterances": ["Khi đang giận, mình thử dừng một nhịp để nhận ra cảm giác rồi mới chọn lời nói."],
                 "source_ids": ["sn36.6-two-arrows"], "withhold": False, "withhold_reason": ""},
                {"output": {"type": "moderation_result", "flagged": False}})


class LiveReplyTests(unittest.TestCase):
    def test_narration_keeps_one_chapter_until_it_closes_and_rewinds_cancelled_clips(self):
        texts = (
            "Buổi sáng một lời nói nhỏ có thể làm mình bực bội suốt cả ngày.",
            "Điều ấy thường lớn lên từ mệt mỏi và cách ta hiểu giọng nói của nhau.",
            "Nếu chỉ tìm người để trách, ta bỏ lỡ cơ hội thấy phản ứng của chính mình.",
            "Trước câu đáp gắt, thử dừng một nhịp và nói điều thật sự cần nói.",
            "Sự điềm tĩnh ấy mở sang cách giữ lời nói vừa thật vừa có ích.",
        )

        class ArcAPI(FakeAPI):
            async def structured(self, **kwargs):
                self.calls.append(kwargs)
                payload = kwargs["payload"]
                speech = ("Một lời thật có thể làm đau nếu nói khi cơn giận còn nóng."
                          if "Chánh ngữ" in payload["topic"] else texts[payload["narrative_part"] - 1])
                return ({"utterances": [speech],
                         "source_ids": [payload["source_cards"][0]["id"]],
                         "withhold": False, "withhold_reason": ""},
                        {"output": {"type": "moderation_result", "flagged": False}})

        api = ArcAPI()
        engine = LiveReplyEngine(api, DharmaCatalog(CATALOG))
        topics = ["Duyên khởi và những điều kiện của một cơn giận",
                  "Chánh ngữ khi cần nói một điều khó nghe"]
        engine._profile = lambda: ReplySettings(topics=topics)
        recent = []
        for step in range(5):
            decision = asyncio.run(engine.narrate(recent))
            self.assertEqual(decision.action, "speak")
            payload = api.calls[-1]["payload"]
            self.assertEqual((payload["topic"], payload["narrative_part"]), (topics[0], step + 1))
            if step == 2:
                self.assertEqual(payload["previous_spoken"], recent[-1])
            engine.note_narration_queued(f"reply-{step}", decision.utterances[0])
            recent.extend(decision.utterances)

        next_decision = asyncio.run(engine.narrate(recent))
        self.assertEqual(next_decision.action, "speak")
        self.assertEqual(api.calls[-1]["payload"]["topic"], topics[1])
        self.assertEqual(api.calls[-1]["payload"]["narrative_part"], 1)

        engine.rewind_cancelled_narration(["reply-3", "reply-4"])
        resumed = asyncio.run(engine.narrate(recent[:3] + ["Một bình luận vừa được đáp xong."]))
        self.assertEqual(resumed.action, "speak")
        self.assertEqual(api.calls[-1]["payload"]["topic"], topics[0])
        self.assertEqual(api.calls[-1]["payload"]["narrative_part"], 4)
        self.assertTrue(api.calls[-1]["payload"]["resume_after_interlude"])

    def test_coordinator_requeues_a_cancelled_chapter_part(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = SimpleNamespace(data_dir=Path(directory), queue_size=8,
                                       demo_sales_mode=True, stream_prebuffer_segments=2)
            engine = LiveReplyEngine(FakeAPI(), DharmaCatalog(CATALOG))
            engine._profile = lambda: ReplySettings(topics=["Hai mũi tên khi gặp chuyện đau lòng"])
            coordinator = LiveReplyCoordinator(
                settings, None, engine, None, None,
                SimpleNamespace(video_queue=queue.Queue(), drop_pending_sales=lambda *_: None),
            )
            coordinator._pause_speech = lambda: False
            coordinator.next_invitation_at = float("inf")
            coordinator.next_affiliate_at = float("inf")

            asyncio.run(coordinator._create_background_once())
            self.assertEqual(engine._arc_beat_index, 1)
            first_job = coordinator.store.db.execute(
                "SELECT id FROM speech_jobs ORDER BY created_at LIMIT 1").fetchone()[0]
            asyncio.run(coordinator._viewer_segment_ready())
            self.assertEqual(coordinator.store.job_status(first_job), "cancelled")
            self.assertEqual(engine._arc_beat_index, 0)

            asyncio.run(coordinator._create_background_once())
            self.assertEqual(engine._arc_beat_index, 1)
            self.assertEqual(coordinator.store.background_backlog(), 1)
            coordinator.store.close()

    def test_unanswered_comment_stays_available_and_ack_needs_media_event(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LiveStateStore(Path(directory) / "comments.sqlite3")
            store.record([StoredComment("one", "An", "Làm sao bớt giận?"),
                          StoredComment("two", "Bình", "Con cũng hay giận"),
                          StoredComment("three", "Chi", "Vô thường là gì?")])
            window = store.claim_window(3)
            decision = ReplyDecision("speak", ("one",), ("two",), ("three",), (),
                                     "practical_question", ("Mình thử dừng một nhịp trước khi nói.",))
            reply_id = store.commit(decision, window)
            self.assertEqual([c.id for c in store.claim_window(3)], ["three"])
            job = store.claim_speech_job()
            self.assertEqual(job.reply_id, reply_id)
            self.assertEqual(store.db.execute("SELECT status FROM comments WHERE id='one'").fetchone()[0], "queued")
            store.mark_feeding(job.id)
            store.record_media_event(job.id, True)
            self.assertEqual(store.db.execute("SELECT status FROM comments WHERE id='one'").fetchone()[0], "fed_to_encoder")
            self.assertEqual(store.db.execute("SELECT status FROM comments WHERE id='two'").fetchone()[0], "fed_to_encoder")
            self.assertEqual(store.db.execute("SELECT status FROM comments WHERE id='three'").fetchone()[0], "claimed")
            store.close()

    def test_restart_marks_in_flight_feed_uncertain_instead_of_repeating(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "comments.sqlite3"
            store = LiveStateStore(path)
            store.record([StoredComment("one", "An", "Vô thường là gì?")])
            store.commit(ReplyDecision("speak", ("one",), (), (), (), "question",
                                       ("Mọi điều thay đổi nên ta trân trọng hiện tại.",)), store.claim_window(1))
            job = store.claim_speech_job()
            store.mark_feeding(job.id)
            store.close()
            store = LiveStateStore(path)
            self.assertEqual(store.job_status(job.id), "uncertain")
            self.assertIsNone(store.claim_speech_job())
            self.assertEqual(store.db.execute("SELECT status FROM comments WHERE id='one'").fetchone()[0], "uncertain")
            store.close()

    def test_second_utterance_waits_for_first_feed_ack(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LiveStateStore(Path(directory) / "comments.sqlite3")
            store.record([StoredComment("one", "An", "Làm sao bớt giận?")])
            store.commit(ReplyDecision("speak", ("one",), (), (), (), "question",
                                       ("Mình thử dừng một nhịp.", "Rồi mới chọn lời nói.")),
                         store.claim_window(1))
            first = store.claim_speech_job()
            self.assertIsNone(store.claim_speech_job())
            store.mark_feeding(first.id)
            store.record_media_event(first.id, True)
            second = store.claim_speech_job()
            self.assertEqual(second.index, 1)
            store.close()

    def test_feeder_failure_is_uncertain_and_not_replayed(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LiveStateStore(Path(directory) / "comments.sqlite3")
            store.record([StoredComment("one", "An", "Làm sao bớt giận?")])
            store.commit(ReplyDecision("speak", ("one",), (), (), (), "question",
                                       ("Mình thử dừng một nhịp.",)), store.claim_window(1))
            job = store.claim_speech_job()
            store.mark_feeding(job.id)
            store.record_media_event(job.id, False)
            self.assertEqual(store.job_status(job.id), "uncertain")
            self.assertIsNone(store.claim_speech_job())
            self.assertEqual(store.db.execute("SELECT status FROM comments WHERE id='one'").fetchone()[0], "uncertain")
            store.close()

    def test_viewer_speech_job_takes_priority_over_prepared_narration(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LiveStateStore(Path(directory) / "comments.sqlite3")
            store.commit(ReplyDecision("speak", reason_code="background",
                                       utterances=("Một lời dẫn đang chờ phát.",)), [])
            store.record([StoredComment("one", "An", "Con đang buồn")])
            store.commit(ReplyDecision("speak", ("one",), reason_code="viewer",
                                       utterances=("Nghe vậy hẳn bạn đang rất mệt.",)),
                         store.claim_window(1))
            self.assertFalse(store.claim_speech_job().is_background)
            store.close()

    def test_luna_selects_related_questions_and_uses_real_source_id(self):
        api = FakeAPI()
        engine = LiveReplyEngine(api, DharmaCatalog(CATALOG))
        comments = [StoredComment("one", "An", "Làm sao bớt giận?"),
                    StoredComment("two", "Bình", "Con cũng hay giận"),
                    StoredComment("three", "Chi", "Vô thường là gì?")]
        decision = asyncio.run(engine.plan(comments, []))
        self.assertEqual(decision.selected_ids, ("one",))
        self.assertEqual(decision.related_ids, ("two",))
        self.assertEqual(decision.deferred_ids, ("three",))
        self.assertEqual(decision.source_ids, ("sn36.6-two-arrows",))
        self.assertEqual([call["name"] for call in api.calls], ["live_comment_selection", "live_dharma_speech"])
        self.assertEqual(api.calls[0]["effort"], "none")
        self.assertEqual(api.calls[1]["effort"], "low")

    def test_unrelated_valid_question_is_deferred_even_if_luna_calls_it_ignored(self):
        class OverIgnoringAPI(FakeAPI):
            async def structured(self, **kwargs):
                if kwargs["name"] == "live_comment_selection":
                    return ({"selected_id": "one", "related_ids": ["two"], "ignored_ids": ["three"],
                             "topic_query": "giận và phản ứng",
                             "reason_code": "Câu hỏi này hay hơn nên chọn trước"}, {})
                return await super().structured(**kwargs)

        engine = LiveReplyEngine(OverIgnoringAPI(), DharmaCatalog(CATALOG))
        comments = [StoredComment("one", "An", "Làm sao bớt giận?"),
                    StoredComment("two", "Bình", "Tôi cũng hay nổi nóng khi bị chê"),
                    StoredComment("three", "Chi", "Tôi mất ngủ do áp lực công việc")]
        decision = asyncio.run(engine.plan(comments, []))
        self.assertEqual(decision.deferred_ids, ("three",))
        self.assertEqual(decision.ignored_ids, ())
        self.assertEqual(decision.reason_code, "relevant_question")

    def test_self_harm_gets_automatic_safe_reply_without_llm_draft(self):
        api = FakeAPI()
        engine = LiveReplyEngine(api, DharmaCatalog(CATALOG))
        decision = asyncio.run(engine.plan([StoredComment("one", "An", "Con không muốn sống nữa")], []))
        self.assertEqual(decision.reason_code, "safety_self_harm")
        self.assertIn("người bạn tin cậy", decision.utterances[0])
        self.assertEqual(api.calls, [])

    def test_identity_question_discloses_avatar_ai_without_claiming_ordained_status(self):
        api = FakeAPI()
        engine = LiveReplyEngine(api, DharmaCatalog(CATALOG))
        decision = asyncio.run(engine.plan([StoredComment("one", "An", "Bạn là ai vậy?")], []))
        self.assertEqual(decision.reason_code, "identity_ai")
        self.assertIn("avatar AI", decision.utterances[0])
        self.assertIn("không phải một vị tu sĩ thật", decision.utterances[0])
        self.assertEqual(api.calls, [])

    def test_opening_and_commercial_mention_are_one_short_clip_each(self):
        self.assertTrue(OPENING.startswith("Nam Mô Bổn Sư Thích Ca Mâu Ni Phật"))
        self.assertNotIn("avatar AI", OPENING)
        self.assertIn("đừng ghi thông tin riêng tư", OPENING)
        self.assertIn("giỏ hàng", AFFILIATE_MENTION)
        self.assertNotIn("tiếp thị", AFFILIATE_MENTION)
        self.assertNotIn("hoa hồng", AFFILIATE_MENTION)
        self.assertNotIn("năng lượng hộ thân", AFFILIATE_MENTION)
        self.assertLessEqual(len(OPENING), 260)
        self.assertLessEqual(len(AFFILIATE_MENTION), 260)

    def test_generated_greeting_is_rewritten_before_live_speech(self):
        class GreetingAPI(FakeAPI):
            async def structured(self, **kwargs):
                self.calls.append(kwargs)
                speech = ("Chào quý vị, hôm nay ta cùng nói về vô thường." if len(self.calls) == 1 else
                          "Khi việc đời đổi thay, mình thử dừng một hơi thở để thấy lòng đang phản ứng ra sao.")
                return ({"utterances": [speech], "source_ids": [], "withhold": False,
                         "withhold_reason": ""},
                        {"output": {"type": "moderation_result", "flagged": False}})

        api = GreetingAPI()
        engine = LiveReplyEngine(api, DharmaCatalog(CATALOG))
        draft = asyncio.run(engine._write({"topic": "vô thường"}, set(), [], narration=True))
        self.assertEqual(len(api.calls), 2)
        self.assertTrue(draft["utterances"][0].startswith("Khi việc đời"))

    def test_moderation_routes_the_flagged_comment_not_another_person(self):
        class RiskAPI(FakeAPI):
            async def moderate(self, texts):
                return [{"categories": {}, "flagged": False},
                        {"categories": {"self-harm/intent": True}, "flagged": True}]

        engine = LiveReplyEngine(RiskAPI(), DharmaCatalog(CATALOG))
        decision = asyncio.run(engine.plan([
            StoredComment("one", "An", "Làm sao bớt giận?"),
            StoredComment("two", "Bình", "Mình đang ở một lúc rất tệ"),
        ], []))
        self.assertEqual(decision.selected_ids, ("two",))
        self.assertEqual(decision.deferred_ids, ("one",))
        self.assertEqual(decision.reason_code, "safety_self_harm")

    def test_invalid_draft_is_withheld_automatically(self):
        class InvalidAPI(FakeAPI):
            async def structured(self, **kwargs):
                self.calls.append(kwargs)
                return ({"utterances": ["https://example.com"], "source_ids": [],
                         "withhold": True, "withhold_reason": "unsafe"}, {})

        api = InvalidAPI()
        engine = LiveReplyEngine(api, DharmaCatalog(CATALOG))
        decision = asyncio.run(engine.plan([StoredComment("one", "An", "Làm sao bớt giận?")], []))
        self.assertEqual(decision.action, "skip")
        self.assertEqual(decision.reason_code, "unsafe_draft_withheld")
        self.assertEqual(len(api.calls), 2)

    def test_doctrinal_answer_without_source_is_withheld(self):
        class NoSourceAPI(FakeAPI):
            async def structured(self, **kwargs):
                self.calls.append(kwargs)
                return ({"utterances": ["Nghiệp là những lựa chọn có chủ ý trong lời nói và việc làm."],
                         "source_ids": [], "withhold": False, "withhold_reason": ""},
                        {"output": {"type": "moderation_result", "flagged": False}})

        engine = LiveReplyEngine(NoSourceAPI(), DharmaCatalog(CATALOG))
        decision = asyncio.run(engine.plan([StoredComment("one", "An", "Nghiệp là gì?")], []))
        self.assertEqual(decision.reason_code, "unsafe_draft_withheld")
        self.assertFalse(decision.utterances)

    def test_answer_is_one_complete_clip_to_avoid_mid_answer_gap(self):
        class TwoClipAPI(FakeAPI):
            async def structured(self, **kwargs):
                self.calls.append(kwargs)
                return ({"utterances": ["Mình nghe câu hỏi của bạn.", "Thử dừng lại một nhịp nhé."],
                         "source_ids": [], "withhold": False, "withhold_reason": ""},
                        {"output": {"type": "moderation_result", "flagged": False}})

        engine = LiveReplyEngine(TwoClipAPI(), DharmaCatalog(CATALOG))
        decision = asyncio.run(engine.plan([StoredComment("one", "An", "Làm sao bớt giận?")], []))
        self.assertEqual(decision.reason_code, "unsafe_draft_withheld")

    def test_commercial_pitch_cannot_be_inserted_into_unrelated_viewer_answer(self):
        class SalesAPI(FakeAPI):
            async def structured(self, **kwargs):
                self.calls.append(kwargs)
                return ({"utterances": ["Quý vị đang buồn thì hãy xem Vòng Chú Lăng Nghiêm trong giỏ hàng."],
                         "source_ids": [], "withhold": False, "withhold_reason": ""},
                        {"output": {"type": "moderation_result", "flagged": False}})

        engine = LiveReplyEngine(SalesAPI(), DharmaCatalog(CATALOG))
        decision = asyncio.run(engine.plan([StoredComment("one", "An", "Tôi đang buồn và khó ngủ")], []))
        self.assertEqual(decision.reason_code, "unsafe_draft_withheld")
        self.assertFalse(decision.utterances)

    def test_product_claim_gate_allows_clear_denial_but_rejects_false_promises(self):
        self.assertFalse(_unsupported_commerce_claim("Chiếc vòng không có phép đổi vận hay hộ thân."))
        self.assertTrue(_unsupported_commerce_claim("Chiếc vòng có năng lượng hộ thân cho quý vị."))
        self.assertTrue(_unsupported_commerce_claim("Các gian hàng đều hỗ trợ hoàn trả vô điều kiện."))

    def test_unknown_doctrine_is_withheld_without_generic_sermon(self):
        engine = LiveReplyEngine(FakeAPI(), DharmaCatalog(CATALOG))
        decision = asyncio.run(engine.plan([
            StoredComment("one", "An", "Pháp môn Tịnh độ này có nguồn nào?")
        ], []))
        self.assertEqual(decision.reason_code, "source_gap_withheld")
        self.assertFalse(decision.utterances)

    def test_responses_request_uses_luna_strict_schema_and_inline_moderation(self):
        requests = []

        def handler(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json={
                "status": "completed",
                "output": [{"type": "message", "content": [{"type": "output_text", "text": '{"ok":true}'}]}],
                "moderation": {"output": {"type": "moderation_result", "flagged": False}},
            })

        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await OpenAIResponses(client, "test-key").structured(
                    instructions="Chỉ trả JSON.", payload={"question": "Giận là gì?"},
                    schema={"type": "object", "properties": {"ok": {"type": "boolean"}},
                            "required": ["ok"], "additionalProperties": False},
                    name="probe", effort="none", max_tokens=80)

        answer, moderation = asyncio.run(run())
        self.assertEqual(answer, {"ok": True})
        self.assertFalse(moderation["output"]["flagged"])
        self.assertEqual(requests[0]["model"], "gpt-6-luna")
        self.assertEqual(requests[0]["reasoning"], {"effort": "none"})
        self.assertEqual(requests[0]["text"]["format"]["strict"], True)
        self.assertEqual(requests[0]["moderation"]["model"], "omni-moderation-latest")
        self.assertFalse(requests[0]["store"])

    def test_viewer_answer_keeps_narration_bridge_until_answer_is_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = SimpleNamespace(data_dir=Path(directory), queue_size=8, fb_batch_size=4,
                                       demo_sales_mode=True, stream_prebuffer_segments=2)
            drops = []
            streamer = SimpleNamespace(video_queue=queue.Queue(),
                                       drop_pending_sales=lambda *_: drops.append(True))

            class ViewerEngine:
                async def plan(self, window, recent):
                    self.assert_bridge()
                    return ReplyDecision("speak", (window[0].id,), reason_code="viewer",
                                         utterances=("Mình nghe điều bạn đang hỏi và thử nhìn nó từ hôm nay.",))

            engine = ViewerEngine()
            coordinator = LiveReplyCoordinator(settings, None, engine, None, None, streamer)
            coordinator._pause_speech = lambda: False
            background_id = coordinator.store.commit(
                ReplyDecision("speak", reason_code="background",
                              utterances=("Một lời dẫn nhẹ nhàng để giữ nhịp trò chuyện.",)), [])
            for _ in range(2):
                coordinator.store.commit(ReplyDecision("speak", reason_code="background",
                    utterances=("Một lời dẫn nữa để chuẩn bị sẵn tiếng nói.",)), [])
            self.assertEqual(coordinator.store.background_backlog(), 3)
            background_job_id = f"{background_id}-00"
            engine.assert_bridge = lambda: self.assertEqual(coordinator.store.job_status(background_job_id), "pending")

            class Source:
                async def fetch(self):
                    return [StoredComment("viewer", "An", "Làm sao bớt giận?")]

            coordinator.source = Source()
            asyncio.run(coordinator._poll_once())
            self.assertEqual(coordinator.store.job_status(background_job_id), "pending")
            asyncio.run(coordinator._create_reply_once())
            self.assertEqual(coordinator.store.job_status(background_job_id), "pending")
            self.assertEqual(drops, [])
            self.assertEqual(coordinator.store.db.execute(
                "SELECT status FROM comments WHERE id='viewer'").fetchone()[0], "queued")
            asyncio.run(coordinator._viewer_segment_ready())
            self.assertEqual(coordinator.store.job_status(background_job_id), "cancelled")
            self.assertEqual(drops, [True])
            self.assertEqual(background_id, coordinator.store.db.execute(
                "SELECT reply_id FROM speech_jobs WHERE id=?", (background_job_id,)).fetchone()[0])
            coordinator.store.close()

    def test_continuity_narration_falls_back_when_luna_is_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = SimpleNamespace(data_dir=Path(directory), queue_size=8,
                                       demo_sales_mode=True, stream_prebuffer_segments=2)

            class BrokenNarrator:
                async def narrate(self, recent):
                    raise RuntimeError("temporary OpenAI failure")

            coordinator = LiveReplyCoordinator(settings, None, BrokenNarrator(), None, None,
                                               SimpleNamespace(video_queue=queue.Queue()))
            coordinator._pause_speech = lambda: False
            with self.assertLogs("fb_avatar_live.live_reply.coordinator", level="ERROR"):
                asyncio.run(coordinator._create_background_once())
            self.assertEqual(coordinator.store.background_backlog(), 1)
            row = coordinator.store.db.execute(
                "SELECT reason_code, model_id FROM replies LIMIT 1").fetchone()
            self.assertEqual(tuple(row), ("continuity_fallback", "local-script"))
            coordinator.store.close()

    def test_affiliate_mention_requires_confirmed_cart_and_quiet_chat(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = SimpleNamespace(data_dir=Path(directory), queue_size=8,
                                       demo_sales_mode=True, stream_prebuffer_segments=2)

            class Narrator:
                async def narrate(self, recent):
                    return ReplyDecision("speak", reason_code="background_dharma",
                                         utterances=("Một ý Phật học cho buổi chia sẻ hôm nay.",))

            coordinator = LiveReplyCoordinator(settings, None, Narrator(), None, None,
                                               SimpleNamespace(video_queue=queue.Queue()))
            coordinator._pause_speech = lambda: False
            coordinator.next_invitation_at = float("inf")
            coordinator.next_affiliate_at = 0
            coordinator.last_viewer_at = 0
            coordinator._affiliate_cart_confirmed = lambda: False
            asyncio.run(coordinator._create_background_once())
            self.assertEqual(coordinator.store.db.execute(
                "SELECT reason_code FROM replies ORDER BY created_at DESC LIMIT 1").fetchone()[0],
                "background_dharma")

            coordinator._affiliate_cart_confirmed = lambda: True
            coordinator.last_viewer_at = time.monotonic()
            asyncio.run(coordinator._create_background_once())
            self.assertEqual(coordinator.store.db.execute(
                "SELECT COUNT(*) FROM replies WHERE reason_code='affiliate_disclosed'").fetchone()[0], 0)

            coordinator.last_viewer_at = 0
            asyncio.run(coordinator._create_background_once())
            self.assertEqual(coordinator.store.db.execute(
                "SELECT COUNT(*) FROM replies WHERE reason_code='affiliate_disclosed'").fetchone()[0], 1)
            coordinator.store.close()

    def test_invitation_is_scheduled_in_continuous_narration(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = SimpleNamespace(data_dir=Path(directory), queue_size=8,
                                       demo_sales_mode=True, stream_prebuffer_segments=2)
            coordinator = LiveReplyCoordinator(settings, None, None, None, None,
                                               SimpleNamespace(video_queue=queue.Queue()))
            coordinator._pause_speech = lambda: False
            coordinator.next_invitation_at = 0
            asyncio.run(coordinator._create_background_once())
            row = coordinator.store.db.execute(
                "SELECT reason_code, utterances_json FROM replies LIMIT 1").fetchone()
            self.assertEqual(row[0], "invitation_to_share")
            self.assertIn("thông tin riêng tư", row[1])
            self.assertGreater(coordinator.next_invitation_at, time.monotonic())
            coordinator.store.close()

    def test_streamer_acks_only_after_both_tracks_succeed(self):
        with tempfile.TemporaryDirectory() as directory:
            streamer = SegmentStreamer(SimpleNamespace(queue_size=2, data_dir=Path(directory)))
            events = []
            streamer.on_segment_result = lambda job_id, success: events.append((job_id, success))
            streamer.publisher_process = SimpleNamespace(poll=lambda: None)
            path = Path(directory) / "clip.mp4"
            path.write_bytes(b"test")
            streamer.enqueue(path, job_id="reply-0")
            streamer._segment_done(path, "video", True)
            self.assertEqual(events, [])
            streamer._segment_done(path, "audio", True)
            self.assertEqual(events, [("reply-0", True)])
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
