from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from meme_pipeline.search import SearchEngine
from meme_pipeline.search.normalize import name_core, normalize


ROOT = Path(__file__).resolve().parents[1]
PYDICT = ROOT / "data/search/pinyin.json"


class SearchEngineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="meme-search-test-")
        self.root = Path(self.temp.name)
        self.aliases = self.root / "aliases.json"
        self.aliases.write_text(
            json.dumps(
                {
                    "concepts": [
                        {
                            "id": "happy",
                            "label": "happy",
                            "terms": ["开心", "高兴", "快乐"],
                            "tags": {"开心": 1.0, "高兴": 1.0},
                        },
                        {
                            "id": "sad",
                            "label": "sad",
                            "terms": ["难过", "伤感"],
                            "tags": {"伤感": 1.0},
                        },
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.entries = [
            {"id": "1", "name": "今天也要开心", "tag": ["开心"]},
            {"id": "2", "name": "高兴雀跃", "tag": ["高兴", "奶蛙"]},
            {"id": "3", "name": "奶蛙空中翻滚大笑", "tag": ["奶蛙", "大笑"]},
            {"id": "4", "name": "有点东西", "tag": ["甩锅"]},
            {"id": "5", "name": "开心到飞起", "tag": ["开心"]},
            {"id": "6", "name": "侧身指你", "tag": ["右"]},
            {"id": "7", "name": "难过", "tag": ["伤感"]},
        ]
        self.engine = SearchEngine(
            self.root,
            self.entries,
            aliases_path=self.aliases,
            pinyin_path=PYDICT,
            require_images=False,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_exact_tag(self) -> None:
        result = self.engine.search("开心", stable=True)
        self.assertEqual(result["method"], "exact_tag")
        self.assertEqual(result["matched_value"], "开心")
        self.assertEqual(result["score"], 1.0)
        self.assertFalse(result["fallback"])
        self.assertIn(result["id"], {"1", "5"})

    def test_alias_tag(self) -> None:
        result = self.engine.search("快乐", stable=True)
        self.assertEqual(result["method"], "alias_tag")
        self.assertEqual(result["canonical"], "happy")
        self.assertIn(result["id"], {"1", "2", "5"})
        self.assertGreaterEqual(result["candidate_count"], 1)

    def test_exact_name(self) -> None:
        result = self.engine.search("有点东西", stable=True)
        self.assertEqual(result["method"], "name_exact")
        self.assertEqual(result["id"], "4")

    def test_name_contains(self) -> None:
        result = self.engine.search("翻滚", stable=True)
        self.assertEqual(result["id"], "3")
        self.assertFalse(result["fallback"])

    def test_pinyin_homophone(self) -> None:
        result = self.engine.search("开新", stable=True)
        self.assertEqual(result["method"], "pinyin_tag")
        self.assertEqual(result["id"], "1")
        self.assertGreaterEqual(result["score"], 0.80)

    def test_synonym_homophone(self) -> None:
        result = self.engine.search("难锅", stable=True)
        self.assertEqual(result["id"], "7")
        self.assertIn(result["method"], {"pinyin_name", "alias_tag"})

    def test_short_pinyin_tag_does_not_match_long_query(self) -> None:
        result = self.engine.search("有点难过", stable=True)
        self.assertNotEqual(result["id"], "6")
        self.assertEqual(result["canonical"], "sad")
        self.assertEqual(result["id"], "7")

    def test_fallback(self) -> None:
        result = self.engine.search("今天天气", seed=7)
        self.assertTrue(result["fallback"])
        self.assertEqual(result["method"], "fallback")
        self.assertIsNone(result["matched_field"])
        self.assertIn(result["id"], {"1", "2", "3", "4", "5", "6", "7"})

    def test_empty_query_falls_back(self) -> None:
        result = self.engine.search("", seed=1)
        self.assertTrue(result["fallback"])
        self.assertEqual(result["fallback_reason"], "empty_query")

    def test_window_and_stable(self) -> None:
        stable = self.engine.search("开心", stable=True)
        self.assertEqual(stable["candidate_count"], 2)
        self.assertEqual(stable["id"], "1")
        picked = self.engine.search("开心", seed=123)
        self.assertIn(picked["id"], {"1", "5"})

    def test_result_is_json_serializable(self) -> None:
        result = self.engine.search("开心", seed=1)
        json.dumps(result, ensure_ascii=False)

    def test_search_many_returns_ranked_matches(self) -> None:
        result = self.engine.search_many("开心", limit=10)
        self.assertFalse(result["fallback"])
        self.assertGreaterEqual(result["total"], 2)
        self.assertEqual(result["items"][0]["method"], "exact_tag")
        self.assertEqual(result["items"][0]["score"], 1.0)
        self.assertEqual([item["id"] for item in result["items"][:2]], ["1", "5"])

    def test_search_many_limit_and_offset(self) -> None:
        first = self.engine.search_many("开心", limit=1)
        second = self.engine.search_many("开心", limit=1, offset=1)
        self.assertEqual(first["total"], second["total"])
        self.assertEqual(first["items"][0]["id"], "1")
        self.assertEqual(second["items"][0]["id"], "5")

    def test_search_many_fallback_is_empty(self) -> None:
        result = self.engine.search_many("今天天气")
        self.assertTrue(result["fallback"])
        self.assertEqual(result["fallback_reason"], "no_match")
        self.assertEqual(result["items"], [])

    def test_name_core(self) -> None:
        self.assertEqual(
            name_core("奶龙表情包动图 可爱的表情包 · · 马上安排 755"),
            "马上安排",
        )
        self.assertEqual(normalize("  开 心！ "), "开心")


if __name__ == "__main__":
    unittest.main()
