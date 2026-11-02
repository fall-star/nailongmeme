from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

_SEPARATORS = re.compile(r"[·|｜/]+")
_EDGE_BOILERPLATE = re.compile(
    r"^(?:奶龙表情包动图|奶龙动态表情包|奶龙表情包|奶龙情头|奶龙表情|小?奶龙|小?奶蛙|可爱的表情包|圣诞表情包|表情包|动图)+"
)
_TRAILING_TEMPLATE = re.compile(r"(?:表情包|动图|可爱的表情包|圣诞表情包)+$")
_TRAILING_CODE = re.compile(r"\s+\d{2,4}$")


def normalize(text: object) -> str:
    """Normalize Chinese/Latin text for case/punctuation-insensitive matching."""
    value = unicodedata.normalize("NFKC", str(text)).casefold()
    value = value.replace("ü", "v").replace("Ü", "v")
    value = re.sub(r"[\s\u3000]+", "", value)
    value = re.sub(r"[^\w\u4e00-\u9fff]+", "", value)
    return value


def name_core(name: object) -> str:
    """Strip common template prefixes, separators and trailing serial numbers."""
    raw = str(name).strip()
    parts = [part.strip() for part in _SEPARATORS.split(raw) if part.strip()]
    candidate = parts[-1] if parts else raw
    candidate = _TRAILING_CODE.sub("", candidate).strip()
    candidate = _EDGE_BOILERPLATE.sub("", candidate).strip()
    candidate = _TRAILING_TEMPLATE.sub("", candidate).strip()
    candidate = re.sub(r"\d+$", "", candidate).strip()
    core = normalize(candidate)
    # If the template stripping leaves nothing useful, keep the full normalized name.
    full = normalize(raw)
    return core if len(core) >= 2 else full


def char_ngrams(text: str, size: int = 2) -> set[str]:
    if not text:
        return set()
    if len(text) <= size:
        return {text}
    return {text[i : i + size] for i in range(len(text) - size + 1)}


def dice(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return 2.0 * len(left & right) / (len(left) + len(right))


def sequence_ratio(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def is_subsequence(needle: tuple[str, ...], haystack: tuple[str, ...]) -> bool:
    if not needle or len(needle) > len(haystack):
        return False
    for start in range(len(haystack) - len(needle) + 1):
        if haystack[start : start + len(needle)] == needle:
            return True
    return False
