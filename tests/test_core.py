from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fb_avatar_live.core import (
    CombinedCommentSource,
    Comment,
    CommentStore,
    ContinuousSalesCommentSource,
    SalesDemoLLM,
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

        source = ContinuousSalesCommentSource()
        first_comment = asyncio.run(source.fetch())[0]
        second_comment = asyncio.run(source.fetch())[0]
        self.assertNotEqual(first_comment.id, second_comment.id)

        llm = SalesDemoLLM()
        first_script = asyncio.run(llm.reply([first_comment]))
        second_script = asyncio.run(llm.reply([second_comment]))
        self.assertTrue(first_script)
        self.assertNotEqual(first_script, second_script)


if __name__ == "__main__":
    unittest.main()
