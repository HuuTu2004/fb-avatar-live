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
    Pipeline,
    SalesDemoLLM,
    ZeroTTSEngine,
    split_utterances,
)


class CoreTests(unittest.TestCase):
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

    def test_sales_demo_keeps_producing_unique_content(self) -> None:
        import asyncio

        llm = SalesDemoLLM()
        first_script = asyncio.run(llm.reply([]))
        second_script = asyncio.run(llm.reply([]))
        self.assertTrue(first_script)
        self.assertNotEqual(first_script, second_script)

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

    def test_sales_demo_prefetches_and_answers_viewer_before_more_sales(self) -> None:
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
                    fb_batch_seconds=0.01, comment_source="demo", demo_sales_mode=True,
                    fb_batch_size=8, stream_prebuffer_segments=2, queue_size=8, speech_chunk_chars=100,
                )
                pipeline.store = CommentStore(Path(directory) / "state.sqlite3")
                pipeline.text_queue = asyncio.Queue()
                pipeline.audio_queue = asyncio.Queue()
                pipeline.synthesizing = 0
                pipeline.rendering = 0
                pipeline.sales_generation = 0
                pipeline.streamer = SimpleNamespace(video_queue=queue.Queue(), drop_pending_sales=lambda: None)
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
                finally:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
                    pipeline.store.close()

        asyncio.run(check())

    def test_sales_prompt_continues_without_repeated_acknowledgement(self) -> None:
        import asyncio

        class Client:
            def __init__(self):
                self.requests = []

            async def post(self, url, **kwargs):
                self.requests.append(kwargs["json"])
                content = (
                    "Dạ, Đầu tiên mình phân loại quần áo sáng màu và tối màu trước khi giặt "
                    "để tiện chọn cách giặt phù hợp, sau đó đọc hướng dẫn trên nhãn từng món. "
                    + "Nói thêm một ý " * 30
                    if len(self.requests) == 1
                    else "Dạ. Sau đó chọn lượng nước giặt phù hợp."
                )
                return SimpleNamespace(
                    raise_for_status=lambda: None,
                    json=lambda: {"choices": [{"message": {"content": content}}]},
                )

        client = Client()
        settings = SimpleNamespace(
            comment_source="demo",
            demo_sales_mode=True,
            llm_base_url="https://example.test/v1",
            llm_api_key="test",
            llm_model="gpt-6-luna",
            llm_system_prompt="Bán nước giặt.",
            llm_max_reply_chars=260,
        )
        llm = ExternalLLM(settings, client)
        first = asyncio.run(llm.reply([]))
        second = asyncio.run(llm.reply([]))

        self.assertEqual(
            first,
            "Đầu tiên mình phân loại quần áo sáng màu và tối màu trước khi giặt "
            "để tiện chọn cách giặt phù hợp, sau đó đọc hướng dẫn trên nhãn từng món.",
        )
        self.assertEqual(second, "Sau đó chọn lượng nước giặt phù hợp.")
        first_prompt = client.requests[0]["messages"][1]["content"]
        second_prompt = client.requests[1]["messages"][1]["content"]
        self.assertNotIn("Hãy tiếp tục giới thiệu sản phẩm", first_prompt)
        self.assertIn(first, second_prompt)
        self.assertEqual(client.requests[0]["reasoning_effort"], "none")
        self.assertEqual(client.requests[0]["max_completion_tokens"], 512)
        self.assertNotEqual(first_prompt.split("Góc gợi ý cho lượt này: ")[1].split(". Có thể")[0],
                            second_prompt.split("Góc gợi ý cho lượt này: ")[1].split(". Có thể")[0])

        asyncio.run(llm.reply([Comment("dashboard-1", "Bạn", "Bao tiền vậy?")]))
        viewer_prompt = client.requests[2]["messages"][1]["content"]
        self.assertIn("Bao tiền vậy?", viewer_prompt)
        self.assertIn("Trả lời trực tiếp", viewer_prompt)
        self.assertNotIn("Góc gợi ý cho lượt này", viewer_prompt)

    def test_sales_reply_retries_near_duplicate(self) -> None:
        import asyncio

        class Client:
            def __init__(self):
                self.calls = 0

            async def post(self, *args, **kwargs):
                self.calls += 1
                reply = (
                    "Hãy phân loại quần áo sáng màu trước khi giặt để giữ màu đẹp hơn."
                    if self.calls < 3 else
                    "Với mẻ đồ ít, hãy cân nhắc lượng nước giặt vừa đủ theo hướng dẫn trên nhãn."
                )
                return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"choices": [{"message": {"content": reply}}]})

        settings = SimpleNamespace(
            comment_source="demo", demo_sales_mode=True, llm_base_url="https://example.test/v1",
            llm_api_key="test", llm_model="gpt-6-luna", llm_system_prompt="Bán nước giặt.",
            llm_max_reply_chars=260,
        )
        client = Client()
        llm = ExternalLLM(settings, client)
        first = asyncio.run(llm.reply([]))
        second = asyncio.run(llm.reply([]))
        self.assertNotEqual(first, second)
        self.assertEqual(client.calls, 3)

    def test_sales_style_without_product_name_retries_brand_mention(self) -> None:
        import asyncio

        class Client:
            def __init__(self):
                self.calls = 0

            async def post(self, *args, **kwargs):
                self.calls += 1
                reply = "Hãy xem túi AVATAR nhé." if self.calls == 1 else "Phân loại đồ sáng và tối trước khi giặt để dễ chăm từng mẻ."
                return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"choices": [{"message": {"content": reply}}]})

        settings = SimpleNamespace(
            comment_source="demo", demo_sales_mode=True, llm_base_url="https://example.test/v1",
            llm_api_key="test", llm_model="gpt-6-luna",
            llm_system_prompt="Giới thiệu túi nước giặt xả AVATAR.", llm_max_reply_chars=260,
        )
        client = Client()
        llm = ExternalLLM(settings, client)
        llm.remaining_styles = ["Nói một mẹo; không đặt câu hỏi, không nhắc tên sản phẩm hay lời mời mua."]
        reply = asyncio.run(llm.reply([]))
        self.assertEqual(client.calls, 2)
        self.assertNotIn("AVATAR", reply)


if __name__ == "__main__":
    unittest.main()
