from __future__ import annotations

import json
from pathlib import Path

from .normalize import is_subsequence, sequence_ratio


class PinyinMap:
    """A small static char-to-pinyin map used only for homophone matching.

    The map is generated offline (data/search/pinyin.json); runtime has no
    pypinyin/model dependency.
    """

    def __init__(self, path: Path | None) -> None:
        self.chars: dict[str, str] = {}
        self.source = ""
        if path and path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                chars = payload.get("chars") if isinstance(payload, dict) else None
                if isinstance(chars, dict):
                    self.chars = {
                        str(char): str(syllable).lower()
                        for char, syllable in chars.items()
                        if len(str(char)) == 1 and str(syllable).isalpha()
                    }
                    self.source = str(payload.get("source", ""))
            except (OSError, json.JSONDecodeError):
                self.chars = {}

    def syllables(self, text: str) -> tuple[str, ...] | None:
        """Return one syllable per Chinese character, or None if not all mapped."""
        if not text:
            return None
        values: list[str] = []
        for char in text:
            syllable = self.chars.get(char)
            if syllable is None:
                return None
            values.append(syllable)
        return tuple(values) if values else None

    @staticmethod
    def similarity(left: tuple[str, ...], right: tuple[str, ...]) -> float:
        if not left or not right:
            return 0.0
        if left == right:
            return 1.0
        if is_subsequence(left, right) or is_subsequence(right, left):
            return 0.90
        return sequence_ratio(" ".join(left), " ".join(right))
