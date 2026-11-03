# tools — 一次性/开发辅助脚本

这些脚本不参与每日搜索或抓取主流程；放在这里避免污染项目根目录。

| 文件 | 用途 | 依赖 |
|---|---|---|
| `apply_vision_tags.py` | 合并历史 qwen_vision 批次结果；只覆盖 1011 张历史图 | 无（Pillow 由运行环境提供） |
| `build_search_aliases.py` | 生成候选同义词概念表 `data/search/aliases.json` | 无 |
| `build_pinyin_map.py` | 用 pypinyin 重新生成静态拼音映射 `data/search/pinyin.json` | `pypinyin`（仅生成时需要） |

## 约定

- 从项目根目录运行：`python3 tools/<script>.py`。
- `apply_vision_tags.py` 会写完整目录并同步投递索引；运行前先备份，且不要用它覆盖新手动 tag。
- `build_search_aliases.py` 会覆盖 `data/search/aliases.json`；该文件需要人工审查，审查后不要盲跑生成器。
- `build_pinyin_map.py` 是可选工具；运行时搜索只读静态 `pinyin.json`，不依赖 pypinyin。

## Production release bundle

```bash
python3 tools/prepare_release.py \
  --ids 1182,1185-1187 \
  --with-frontend \
  --output release/20261104
```

This creates a local bundle with API code, catalog metadata, selected image files and optionally `frontend/dist`. It never contacts the server. Review `release/release.json` before copying it to production. The API code must be deployed before a catalog containing `draft` entries.
