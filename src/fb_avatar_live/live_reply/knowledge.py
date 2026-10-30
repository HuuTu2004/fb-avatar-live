from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path


def _fold(value: str) -> str:
    chars = unicodedata.normalize("NFD", value.casefold())
    return re.sub(r"[^a-z0-9 ]+", " ", "".join(char for char in chars if not unicodedata.combining(char)))


class DharmaCatalog:
    """Small source-checked catalog; returns authored summaries, never scraped scripture text."""

    def __init__(self, path: Path):
        self.path = path
        self.cards = {item["id"]: item for item in json.loads(path.read_text(encoding="utf-8"))}
        self._index = {identifier: [_fold(item) for item in card["keywords"]]
                       for identifier, card in self.cards.items()}

    def search(self, query: str, limit: int = 3) -> list[dict]:
        folded = _fold(query)
        scored = []
        for identifier, keywords in self._index.items():
            score = sum(3 for keyword in keywords if f" {keyword} " in f" {folded} ")
            if score:
                scored.append((score, identifier))
        scored.sort(reverse=True)
        return [self.cards[identifier] for _, identifier in scored[:limit]]

    def get(self, identifier: str) -> dict | None:
        return self.cards.get(identifier)
