#!/usr/bin/env python3
"""Regenerate the static char-to-pinyin map used by homophone search.

Optional dev dependency only; runtime search does not import pypinyin.

    python3 -m pip install pypinyin
    python3 tools/build_pinyin_map.py
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    try:
        from pypinyin import Style, lazy_pinyin
    except ImportError:
        raise SystemExit("pypinyin is required only to regenerate the map: python3 -m pip install pypinyin")

    chars: dict[str, str] = {}
    for codepoint in range(0x3400, 0xA000):
        char = chr(codepoint)
        try:
            values = lazy_pinyin(char, style=Style.NORMAL, errors="ignore")
        except Exception:
            continue
        if not values:
            continue
        value = values[0].strip().lower().replace("ü", "v")
        if value and re.fullmatch(r"[a-z]+", value):
            chars[char] = value
    payload = {
        "schema_version": 1,
        "source": "pypinyin",
        "note": "Static char-to-pinyin map for homophone-tolerant search; no runtime model dependency.",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "count": len(chars),
        "chars": chars,
    }
    output = ROOT / "data/search/pinyin.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"wrote {output.relative_to(ROOT)}: {len(chars)} chars")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
