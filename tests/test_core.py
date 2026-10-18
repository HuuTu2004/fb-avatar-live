from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fb_avatar_live.core import (
    CombinedCommentSource,
    Comment,
    CommentStore,
    ExternalLLM,
    FacebookPersonalCommentSource,
    Pipeline,
    SalesDemoLLM,
    ZeroTTSEngine,
    split_utterances,
)


class CoreTests(unittest.TestCase):
    def test_ssn_reader_accepts_only_facebook_chat_and_strips_markup(self) -> None:
        raw = '{"type":"facebook","id":"comment-1","chatname":"An","chatmessage":"<b>Vô thường</b> là gì?","textonly":false}'
        comment = FacebookPersonalCommentSource.parse_message(raw)
        self.assertEqual((comment.id, comment.author, comment.message),
                         ("facebook-personal:comment-1", "An", "Vô thường là gì?"))
        self.assertIsNone(FacebookPersonalCommentSource.parse_message('{"type":"youtube","chatmessage":"Hi"}'))
        self.assertIsNone(FacebookPersonalCommentSource.parse_message('{"type":"facebook","event":"follow","chatmessage":"Hi"}'))

    def test_split_utterances_preserves_words_and_limit(self) -> None:
        text = "Xin chào mọi người. " + "Một câu khá dài " * 12 + "Cảm ơn nhé!"
        chunks = split_utterances(text, max_chars=60)
        self.assertGreater(len(chunks), 2)
        self.assertTrue(all(len(chunk) <= 60 for chunk in chunks))
        self.assertEqual(" ".join(chunks).split(), text.split())

    def test_comment_store_deduplicates_and_marks_batch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = CommentStore(Path(directory) / "state.sqlite3")
            comments = [Comment("1", "An", "Chào"), Comment("2", "Bình", "Hello")]
            self.assertEqual(store.add(comments), 2)
            self.assertEqual(store.add(comments), 0)
            self.assertEqual([item.id for item in store.pending(10)], ["1", "2"])
            store.mark_processed(["1"])
            self.assertEqual([item.id for item in store.pending(10)], ["2"])
            store.close()

    def test_dashboard_comment_goes_before_old_sales_prompts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = CommentStore(Path(directory) / "state.sqlite3")
            store.add([Comment("sales-demo-old", "Demo", "Tiếp tục"), Comment("viewer-1", "An", "Chào")])
            store.add([Comment("dashboard-1", "Bạn", "Bao tiền vậy?")])
            self.assertEqual([item.id for item in store.pending(2)], ["dashboard-1", "viewer-1"])
            store.close()

    def test_combined_comment_source_merges_channels(self) -> None:
        class Source:
            def __init__(self, comment: Comment):
                self.comment = comment

            async def fetch(self) -> list[Comment]:
                return [self.comment]

        import asyncio

        source = CombinedCommentSource(Source(Comment("facebook:1", "An", "A")), Source(Comment("tiktok:2", "Bình", "B")))
        self.assertEqual([item.id for item in asyncio.run(source.fetch())], ["facebook:1", "tiktok:2"])

    def test_dharma_demo_keeps_producing_unique_content(self) -> None:
        import asyncio

        llm = SalesDemoLLM()
        spoken = [asyncio.run(llm.reply([])) for _ in range(len(llm.scripts) + 1)]
        self.assertEqual(len(set(spoken[:-1])), len(llm.scripts))
        self.assertNotEqual(spoken[-2], spoken[-1])
        self.assertTrue(any("Tứ diệu đế" in script for script in llm.scripts))
        self.assertTrue(any("Bát chánh đạo" in script for script in llm.scripts))
        self.assertFalse(any("nước giặt" in script for script in llm.scripts))

    def test_zerotts_cuda_does_not_silently_fall_back_to_cpu(self) -> None:
        import sys
        from unittest.mock import patch

        class FakeZeroTTS:
            providers = None

            @classmethod
            def from_pretrained(cls, model_dir, **kwargs):
                cls.providers = kwargs["providers"]
                return SimpleNamespace(prefix_step_sess=SimpleNamespace(get_providers=lambda: ["CPUExecutionProvider"]))

        settings = SimpleNamespace(zerotts_provider="cuda", zerotts_model_dir="model", zerotts_threads=2)
        with patch.dict(sys.modules, {"zerotts": SimpleNamespace(ZeroTTS=FakeZeroTTS)}):
            with self.assertRaisesRegex(RuntimeError, "CUDA provider did not start"):
                ZeroTTSEngine(settings)
        self.assertEqual(FakeZeroTTS.providers[0], "CUDAExecutionProvider")

    def test_continuous_mode_answers_facebook_comment_before_more_narration(self) -> None:
        import asyncio
        import contextlib
        import queue

        class LLM:
            def __init__(self):
                self.calls = []

            async def reply(self, comments):
                self.calls.append(comments)
                return "Một câu ngắn để phát."

        async def check() -> None:
            with tempfile.TemporaryDirectory() as directory:
                pipeline = Pipeline.__new__(Pipeline)
                pipeline.settings = SimpleNamespace(
                    fb_batch_seconds=0.01, comment_source="facebook", demo_sales_mode=True,
                    fb_batch_size=8, stream_prebuffer_segments=2, queue_size=8, speech_chunk_chars=100,
                )
                pipeline.store = CommentStore(Path(directory) / "state.sqlite3")
                pipeline.text_queue = asyncio.Queue()
                pipeline.audio_queue = asyncio.Queue()
                pipeline.synthesizing = 0
                pipeline.rendering = 0
                pipeline.sales_generation = 0
                dropped_sales = []
                pipeline.streamer = SimpleNamespace(
                    video_queue=queue.Queue(),
                    drop_pending_sales=lambda: dropped_sales.append(True),
                )
                pipeline.llm = LLM()
                task = asyncio.create_task(pipeline.create_replies())
                try:
                    async def wait_for_calls(count):
                        for _ in range(100):
                            if len(pipeline.llm.calls) >= count:
                                return
                            await asyncio.sleep(0.01)
                        self.fail(f"Expected {count} LLM calls")

                    await wait_for_calls(3)
                    self.assertEqual(len(pipeline.llm.calls), 3)
                    self.assertTrue(all(not call for call in pipeline.llm.calls))
                    pipeline.store.add([
                        Comment("dashboard-1", "Bạn", "Bao tiền vậy?"),
                        Comment("dashboard-2", "Bạn", "Giặt tay dùng thế nào?"),
                    ])
                    await wait_for_calls(5)
                    self.assertEqual(pipeline.llm.calls[3][0].message, "Bao tiền vậy?")
                    self.assertEqual(pipeline.llm.calls[4][0].message, "Giặt tay dùng thế nào?")
                    self.assertEqual(pipeline.text_queue.qsize(), 2)
                    self.assertFalse(pipeline.text_queue.get_nowait().is_sales)
                    self.assertFalse(pipeline.text_queue.get_nowait().is_sales)
                    self.assertEqual(dropped_sales, [])
                finally:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
                    pipeline.store.close()

        asyncio.run(check())

    def test_dharma_prompt_rotates_topics_and_answers_comment(self) -> None:
        import asyncio

        class Client:
            def __init__(self):
                self.requests = []

            async def post(self, url, **kwargs):
                self.requests.append(kwargs["json"])
                content = (
                    "Dạ, Vô thường nhắc mình rằng mọi thứ đều đổi thay. "
                    + "Nói thêm một ý " * 30
                    if len(self.requests) == 1
                    else "Dạ. Mình có thể bắt đầu bằng cách nhận biết một hơi thở."
                )
                return SimpleNamespace(
                    raise_for_status=lambda: None,
                    json=lambda: {"choices": [{"message": {"content": content}}]},
                )

        client = Client()
        settings = SimpleNamespace(
            comment_source="demo", demo_sales_mode=True,
            llm_base_url="https://example.test/v1", llm_api_key="test",
            llm_model="gpt-6-luna", llm_system_prompt="Chia sẻ Phật pháp.",
            llm_max_reply_chars=260,
        )
        llm = ExternalLLM(settings, client)
        first = asyncio.run(llm.reply([]))
        second = asyncio.run(llm.reply([]))

        self.assertLessEqual(len(first), 260)
        self.assertFalse(first.lower().startswith("dạ"))
        self.assertEqual(second, "Mình có thể bắt đầu bằng cách nhận biết một hơi thở.")
        first_prompt = client.requests[0]["messages"][1]["content"]
        second_prompt = client.requests[1]["messages"][1]["content"]
        self.assertIn("Phật pháp", first_prompt)
        self.assertIn("không bịa lời Phật", first_prompt)
        self.assertNotIn("nước giặt", first_prompt)
        self.assertIn(first, second_prompt)
        self.assertEqual(client.requests[0]["reasoning_effort"], "none")
        self.assertEqual(client.requests[0]["max_completion_tokens"], 512)
        self.assertNotEqual(first_prompt.split("Chủ đề bắt buộc lượt này: ")[1].split(". Tình huống")[0],
                            second_prompt.split("Chủ đề bắt buộc lượt này: ")[1].split(". Tình huống")[0])

        asyncio.run(llm.reply([Comment("dashboard-1", "Bạn", "Vô thường là gì?")]))
        viewer_prompt = client.requests[2]["messages"][1]["content"]
        self.assertIn("Vô thường là gì?", viewer_prompt)
        self.assertIn("Ưu tiên trả lời trực tiếp", viewer_prompt)
        self.assertNotIn("Chủ đề bắt buộc lượt này", viewer_prompt)

    def test_dharma_topics_and_situations_do_not_repeat_until_exhausted(self) -> None:
        import asyncio

        class Client:
            def __init__(self):
                self.prompts = []

            async def post(self, *args, **kwargs):
                self.prompts.append(kwargs["json"]["messages"][1]["content"])
                text = f"ý{len(self.prompts)}"
                return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"choices": [{"message": {"content": text}}]})

        settings = SimpleNamespace(
            comment_source="demo", demo_sales_mode=True, llm_base_url="https://example.test/v1",
            llm_api_key="test", llm_model="gpt-6-luna", llm_system_prompt="Chia sẻ Phật pháp.",
            llm_max_reply_chars=260,
        )
        client = Client()
        llm = ExternalLLM(settings, client)

        async def run():
            for _ in range(len(llm.sales_angles) * len(llm.situations) + 1):
                await llm.reply([])

        asyncio.run(run())
        pairs = [
            (prompt.split("Chủ đề bắt buộc lượt này: ")[1].split(". Tình huống")[0],
             prompt.split("Tình huống minh họa nếu phù hợp: ")[1].split(". Kiểu nói")[0])
            for prompt in client.prompts
        ]
        self.assertEqual(len(set(pairs[:-1])), len(llm.sales_angles) * len(llm.situations))
        self.assertNotEqual(pairs[-2], pairs[-1])
        self.assertTrue(all(pairs[i][0] != pairs[i + 1][0] for i in range(len(pairs) - 1)))

    def test_dharma_reply_retries_near_duplicate(self) -> None:
        import asyncio

        class Client:
            def __init__(self):
                self.calls = 0

            async def post(self, *args, **kwargs):
                self.calls += 1
                reply = (
                    "Vô thường nhắc mình rằng mọi sự đều thay đổi theo thời gian."
                    if self.calls < 3 else
                    "Khi tâm xao động, mình thử nhận biết hơi thở rồi quay về hiện tại."
                )
                return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"choices": [{"message": {"content": reply}}]})

        settings = SimpleNamespace(
            comment_source="demo", demo_sales_mode=True, llm_base_url="https://example.test/v1",
            llm_api_key="test", llm_model="gpt-6-luna", llm_system_prompt="Chia sẻ Phật pháp.",
            llm_max_reply_chars=260,
        )
        client = Client()
        llm = ExternalLLM(settings, client)
        first = asyncio.run(llm.reply([]))
        second = asyncio.run(llm.reply([]))
        self.assertNotEqual(first, second)
        self.assertEqual(client.calls, 3)

    def test_dharma_style_without_question_retries_question(self) -> None:
        import asyncio

        class Client:
            def __init__(self):
                self.calls = 0

            async def post(self, *args, **kwargs):
                self.calls += 1
                reply = "Bạn có đang chú ý hơi thở?" if self.calls == 1 else "Mình có thể nhận biết hơi thở đang vào và ra."
                return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"choices": [{"message": {"content": reply}}]})

        settings = SimpleNamespace(
            comment_source="demo", demo_sales_mode=True, llm_base_url="https://example.test/v1",
            llm_api_key="test", llm_model="gpt-6-luna",
            llm_system_prompt="Chia sẻ Phật pháp.", llm_max_reply_chars=260,
        )
        client = Client()
        llm = ExternalLLM(settings, client)
        llm.remaining_styles = ["Giải thích một ý; không đặt câu hỏi."]
        reply = asyncio.run(llm.reply([]))
        self.assertEqual(client.calls, 2)
        self.assertNotIn("?", reply)


if __name__ == "__main__":
    unittest.main()
