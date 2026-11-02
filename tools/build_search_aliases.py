#!/usr/bin/env python3
"""Generate a reviewable synonym-concept table for tag/name search.

This file is intentionally a *candidate* table.  The user will review it
manually and may edit data/search/aliases.json afterwards.  Re-running this
script overwrites that file, so do not run it blindly after review.

Usage:
    python3 tools/build_search_aliases.py
    python3 tools/build_search_aliases.py --check
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


CONCEPTS: list[dict] = [
    {
        "id": "happy",
        "label": "开心 / 高兴",
        "terms": ["开心", "高兴", "快乐", "喜悦", "兴奋", "愉快", "欢乐", "好开心"],
        "tags": {"开心": 1.0, "高兴": 1.0, "快乐": 0.95, "喜悦": 0.92, "兴奋": 0.88},
    },
    {
        "id": "laugh",
        "label": "大笑 / 笑",
        "terms": ["大笑", "爆笑", "狂笑", "傻笑", "憨笑", "贱笑", "憋笑", "笑死"],
        "tags": {"大笑": 1.0, "傻笑": 0.90, "憨笑": 0.90, "贱笑": 0.86, "憋笑": 0.84, "强颜欢笑": 0.70, "笑里藏刀": 0.64},
    },
    {
        "id": "smile",
        "label": "微笑 / 保持微笑",
        "terms": ["微笑", "保持微笑", "会心一笑", "笑一笑"],
        "tags": {"微笑": 1.0, "保持微笑": 0.95, "会心一笑": 0.90, "强颜欢笑": 0.74},
    },
    {
        "id": "sad",
        "label": "伤感 / 难过",
        "terms": ["伤感", "难过", "伤心", "悲伤", "失落", "心酸", "忧郁", "幽怨", "孤独", "命苦"],
        "tags": {"伤感": 1.0, "失落": 0.94, "心酸": 0.92, "忧郁": 0.88, "幽怨": 0.82, "孤独": 0.80, "命苦": 0.72},
    },
    {
        "id": "cry",
        "label": "大哭 / 委屈 / 崩溃",
        "terms": ["大哭", "痛哭", "委屈", "崩溃", "扎心", "想哭"],
        "tags": {"大哭": 1.0, "委屈": 0.94, "崩溃": 0.90, "扎心": 0.84},
    },
    {
        "id": "speechless",
        "label": "无语 / 无奈",
        "terms": ["无语", "无奈", "服了", "没话说", "不知说什么"],
        "tags": {"无语": 1.0, "无奈": 0.95, "敷衍": 0.72},
    },
    {
        "id": "blank",
        "label": "面无表情 / 呆滞",
        "terms": ["面无表情", "呆滞", "发呆", "面瘫", "无表情", "愣住"],
        "tags": {"面无表情": 1.0, "呆滞": 0.95, "发呆": 0.88},
    },
    {
        "id": "awkward",
        "label": "尴尬 / 心虚",
        "terms": ["尴尬", "心虚", "不知所措", "违和", "社死"],
        "tags": {"尴尬": 1.0, "心虚": 0.90, "不知所措": 0.86, "违和": 0.72},
    },
    {
        "id": "confused",
        "label": "疑惑 / 懵",
        "terms": ["疑惑", "懵", "懵圈", "懵逼", "懵了", "迷茫", "不解"],
        "tags": {"疑惑": 1.0, "懵圈": 0.96, "懵逼": 0.94, "懵了": 0.92, "迷茫": 0.78},
    },
    {
        "id": "angry",
        "label": "生气 / 愤怒",
        "terms": ["生气", "愤怒", "发火", "暴躁", "烦躁", "气鼓鼓", "不爽", "不高兴", "不服", "气死你"],
        "tags": {"生气": 1.0, "愤怒": 0.96, "发火": 0.94, "暴躁": 0.90, "烦躁": 0.84, "气鼓鼓": 0.86, "不爽": 0.82, "不高兴": 0.82, "不服": 0.78, "气死你": 0.72},
    },
    {
        "id": "surprised",
        "label": "震惊 / 惊讶",
        "terms": ["震惊", "惊讶", "惊呆", "惊呆了", "惊恐", "惊吓", "惊掉下巴", "吃惊"],
        "tags": {"震惊": 1.0, "惊讶": 0.95, "惊呆": 0.94, "惊呆了": 0.92, "惊恐": 0.86, "惊吓": 0.84, "惊掉下巴": 0.82},
    },
    {
        "id": "cute",
        "label": "可爱 / 卖萌",
        "terms": ["可爱", "卖萌", "呆萌", "乖巧", "撒娇", "俏皮", "憨", "憨憨", "萌"],
        "tags": {"可爱": 1.0, "卖萌": 0.96, "呆萌": 0.92, "乖巧": 0.88, "撒娇": 0.86, "俏皮": 0.84, "憨": 0.72, "憨憨": 0.76},
    },
    {
        "id": "sleep",
        "label": "睡觉 / 困",
        "terms": ["睡觉", "困", "困了", "困倦", "赖床", "呼呼大睡", "打哈欠", "想睡"],
        "tags": {"睡觉": 1.0, "困了": 0.95, "困倦": 0.92, "赖床": 0.90, "呼呼大睡": 0.88, "打哈欠": 0.84},
    },
    {
        "id": "food",
        "label": "吃饭 / 馋",
        "terms": ["吃饭", "干饭", "吃货", "贪吃", "馋", "馋了", "馋嘴", "眼馋", "吃饱了"],
        "tags": {"贪吃": 1.0, "吃货": 0.94, "干饭": 0.94, "馋了": 0.90, "馋嘴": 0.88, "眼馋": 0.84, "吃饱了": 0.78},
    },
    {
        "id": "affection",
        "label": "表白 / 喜欢",
        "terms": ["表白", "喜欢", "亲嘴", "求婚", "送戒指", "比心", "爱你"],
        "tags": {"表白": 1.0, "奶龙表白": 0.96, "亲嘴": 0.90, "求婚": 0.88, "送戒指": 0.84},
    },
    {
        "id": "dance",
        "label": "跳舞 / 舞蹈",
        "terms": ["跳舞", "舞蹈", "蹦迪", "钢管舞", "舞狮", "做体操", "摇摆"],
        "tags": {"舞蹈": 1.0, "蹦迪": 0.94, "钢管舞": 0.90, "跳舞": 0.94, "舞狮": 0.86, "做体操": 0.82, "摇摆": 0.78},
    },
    {
        "id": "fight",
        "label": "打架 / 拳击",
        "terms": ["打架", "拳击", "泰拳", "战斗", "对决", "开枪", "拔刀", "打一架", "威胁"],
        "tags": {"拳击": 1.0, "泰拳": 0.95, "战斗": 0.92, "对决": 0.88, "开枪": 0.86, "拔刀": 0.84, "蓄意轰拳": 0.82, "物理超度": 0.74, "威胁": 0.70},
    },
    {
        "id": "run",
        "label": "跑 / 溜",
        "terms": ["跑", "奔跑", "快跑", "逃跑", "跑路", "溜了", "狂奔"],
        "tags": {"奔跑": 1.0, "快跑": 0.95, "逃跑": 0.92, "跑路": 0.90, "溜了": 0.88, "狂奔": 0.86},
    },
    {
        "id": "observe",
        "label": "暗中观察 / 偷看",
        "terms": ["观察", "暗中观察", "偷看", "偷听", "斜眼", "翻白眼"],
        "tags": {"暗中观察": 1.0, "偷看": 0.90, "偷听": 0.88, "斜眼": 0.82, "翻白眼": 0.74},
    },
    {
        "id": "approval",
        "label": "点赞 / 收到 / 问好",
        "terms": ["点赞", "比耶", "收到", "好的", "积极响应", "敬礼", "问好", "打招呼"],
        "tags": {"点赞": 1.0, "比耶": 0.92, "收到": 0.90, "好的": 0.88, "积极响应": 0.84, "敬礼": 0.84, "问好": 0.82},
    },
    {
        "id": "money",
        "label": "钱 / 贪财",
        "terms": ["钱", "要钱", "贪财", "爱财", "撒钱", "有钱", "红包"],
        "tags": {"要钱": 1.0, "贪财": 0.94, "爱财": 0.92, "撒钱": 0.88, "有钱": 0.86, "发红包": 0.76},
    },
    {
        "id": "work",
        "label": "上班 / 社畜",
        "terms": ["上班", "社畜", "不想上班", "不想上学", "努力加载", "加班"],
        "tags": {"社畜": 1.0, "不想上班": 0.95, "不想上学": 0.90, "努力加载": 0.82, "先别急": 0.64, "别催": 0.64},
    },
    {
        "id": "abstract",
        "label": "抽象 / 搞怪",
        "terms": ["抽象", "魔性", "沙雕", "搞怪", "搞笑", "恶搞", "无厘头", "离谱", "中二", "达达主义"],
        "tags": {"抽象": 1.0, "魔性": 0.95, "沙雕": 0.92, "搞怪": 0.90, "搞笑": 0.88, "恶搞": 0.86, "无厘头": 0.80, "离谱": 0.78, "中二": 0.74, "达达主义": 0.68},
    },
    {
        "id": "cool",
        "label": "装酷 / 自信 / 得瑟",
        "terms": ["装酷", "耍帅", "自信", "霸气", "潇洒", "得瑟", "嚣张", "傲娇", "自恋", "臭美"],
        "tags": {"装酷": 1.0, "耍帅": 0.94, "自信": 0.90, "霸气": 0.90, "潇洒": 0.88, "得瑟": 0.86, "嚣张": 0.84, "傲娇": 0.82, "自恋": 0.80, "臭美": 0.76},
    },
    {
        "id": "disgust",
        "label": "嫌弃 / 嘲讽",
        "terms": ["嫌弃", "不屑", "讨厌", "嘲讽", "挑衅", "质疑", "质问", "翻白眼"],
        "tags": {"嫌弃": 1.0, "不屑": 0.96, "讨厌": 0.92, "嘲讽": 0.90, "挑衅": 0.86, "质疑": 0.82, "质问": 0.80, "翻白眼": 0.74},
    },
    {
        "id": "fear",
        "label": "害怕 / 惊恐",
        "terms": ["害怕", "惊恐", "恐惧", "小心翼翼", "虚弱"],
        "tags": {"害怕": 1.0, "惊恐": 0.95, "小心翼翼": 0.78, "虚弱": 0.72},
    },
    {
        "id": "tired",
        "label": "疲惫 / 摆烂",
        "terms": ["疲惫", "累", "慵懒", "躺平", "摆烂", "生无可恋", "虚弱"],
        "tags": {"疲惫": 1.0, "慵懒": 0.94, "躺平": 0.90, "摆烂": 0.88, "生无可恋": 0.82, "虚弱": 0.74},
    },
    {
        "id": "positive",
        "label": "期待 / 治愈",
        "terms": ["期待", "满足", "乐观", "治愈", "舒服", "开心"],
        "tags": {"期待": 1.0, "满足": 0.92, "乐观": 0.90, "治愈": 0.86, "舒服": 0.80},
    },
    {
        "id": "holiday",
        "label": "生日 / 过年",
        "terms": ["生日", "生日快乐", "过年", "拜年", "红包"],
        "tags": {"生日快乐": 1.0, "奶龙拜年": 0.92, "发红包": 0.82},
    },
    {
        "id": "naowa",
        "label": "奶蛙（实体）",
        "terms": ["奶蛙", "蛙蛙"],
        "tags": {"奶蛙": 1.0},
    },
    {
        "id": "nailong",
        "label": "奶龙（实体）",
        "terms": ["奶龙", "龙龙"],
        "tags": {"奶龙": 1.0, "奶龙拜年": 0.70},
    },
    {
        "id": "naidan",
        "label": "奶蛋（实体）",
        "terms": ["奶蛋", "蛋蛋"],
        "tags": {"奶蛋": 1.0},
    },
    {
        "id": "jiahao",
        "label": "嘉豪（人物）",
        "terms": ["嘉豪"],
        "tags": {"嘉豪": 1.0},
    },
    {
        "id": "letters",
        "label": "字母",
        "terms": ["字母", "字母奶龙"],
        "tags": {"字母": 1.0},
    },
    {
        "id": "crown",
        "label": "加冕 / 称帝",
        "terms": ["加冕", "称帝", "登基", "神圣"],
        "tags": {"加冕": 1.0, "称帝": 0.94, "神圣": 0.76},
    },
    {
        "id": "cos",
        "label": "cos / 头套",
        "terms": ["cos", "cosplay", "头套", "猫耳", "fumo"],
        "tags": {"cos": 1.0, "猫耳": 0.84, "fumo": 0.82, "acid": 0.66},
    },
    {
        "id": "strong",
        "label": "强壮 / 肌肉",
        "terms": ["强壮", "肌肉", "大力士", "拳击", "秀肌肉"],
        "tags": {"大力士": 1.0, "秀肌肉": 0.94, "拳击": 0.80},
    },
    {
        "id": "silent_laugh",
        "label": "憋笑 / 强颜欢笑",
        "terms": ["憋笑", "强颜欢笑", "苦笑", "假笑"],
        "tags": {"憋笑": 1.0, "强颜欢笑": 0.92, "笑里藏刀": 0.72},
    },
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="validate only; do not write")
    parser.add_argument("--output", default="data/search/aliases.json")
    args = parser.parse_args(argv)

    catalog = json.loads((ROOT / "data/catalog.json").read_text(encoding="utf-8"))
    counts = Counter(tag for entry in catalog for tag in entry["tag"])
    known = set(counts)
    errors: list[str] = []
    warnings: list[str] = []
    seen_concepts: set[str] = set()
    tag_to_concepts: dict[str, list[str]] = {}

    for concept in CONCEPTS:
        if concept["id"] in seen_concepts:
            errors.append(f"duplicate concept id: {concept['id']}")
        seen_concepts.add(concept["id"])
        kept_tags = {}
        for tag, weight in concept["tags"].items():
            if tag not in known:
                warnings.append(f"{concept['id']}: tag not present in catalog, skipped: {tag!r}")
                continue
            if not isinstance(weight, (int, float)) or not 0 < weight <= 1:
                errors.append(f"{concept['id']}: bad weight for {tag!r}: {weight!r}")
                continue
            kept_tags[tag] = weight
            tag_to_concepts.setdefault(tag, []).append(concept["id"])
        concept["tags"] = kept_tags

    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    if warnings:
        print("\n".join(warnings), file=sys.stderr)

    inventory = []
    for tag, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
        concepts = tag_to_concepts.get(tag, [])
        inventory.append(
            {
                "tag": tag,
                "count": count,
                "concepts": concepts,
                "review": "pending" if concepts else "unmapped",
            }
        )

    payload = {
        "schema_version": 1,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "generator": "tools/build_search_aliases.py",
        "review_status": "pending_user_review",
        "notes": [
            "这是候选同义词表，不是最终语义标准；请逐项审查。",
            "tag weights 表示该 tag 对该概念的接近程度；1.0 为核心 tag。",
            "没有列出的 tag 仍可被精确/包含/字符串模糊搜索命中，只是没有同义词扩展。",
            "不要把奶蛙、奶龙、奶蛋等实体标签合并成情绪标签。",
        ],
        "concepts": CONCEPTS,
        "tag_inventory": inventory,
    }
    if args.check:
        print(f"OK: {len(CONCEPTS)} concepts, {len(inventory)} tags, {len(tag_to_concepts)} mapped")
        return 0

    output = ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {output.relative_to(ROOT)}: {len(CONCEPTS)} concepts, {len(inventory)} tags, {len(tag_to_concepts)} mapped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
