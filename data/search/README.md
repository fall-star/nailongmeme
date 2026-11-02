# data/search — 本地模糊搜索数据

这个目录只存放搜索用的静态数据，不参与抓取/图片校验。

## aliases.json

- **状态：`pending_user_review`，等待人工审查。**
- 由 [`tools/build_search_aliases.py`](../../tools/build_search_aliases.py) 生成。
- 38 个候选概念，覆盖 183 个 tag；其余 130 个 tag 仍是未映射状态。
- 每个概念包含：
  - `terms`：用户 query 可能使用的词；
  - `tags`：该概念下实际存在的 tag 及权重（1.0 为核心）；
  - `label`：概念说明。
- `tag_inventory` 列出全部 313 个 tag、出现次数、所属概念；`unmapped` 表示还没有同义词扩展，但仍可被精确/包含/字符串模糊匹配命中。
- 允许一个 tag 出现在多个概念中（如 `强颜欢笑` 同时属于笑/苦笑相关概念），搜索时取最高分。
- 重新运行生成器会覆盖人工修改，审查后不要盲跑；如需重建，先用备份/diff 对比。

## pinyin.json

- 静态汉字→拼音映射，约 26,703 个常用汉字，322KB。
- 用于同音错字匹配（例如 `开新` → `开心`）。
- 运行时不需要 `pypinyin`，也不加载模型。
- 由 [`tools/build_pinyin_map.py`](../../tools/build_pinyin_map.py) 可选生成；生成时需要 `pip install pypinyin`，但运行搜索不需要。

## 搜索实现

核心在 [`meme_pipeline/search/`](../../meme_pipeline/search/)：

- `normalize.py`：归一化、`name_core`、字符 n-gram、编辑相似度；
- `pinyin.py`：静态拼音映射与同音比较；
- `aliases.py`：概念表加载/查询扩展；
- `engine.py`：评分、阈值、最高分组随机选择。

命令行：

```bash
./run.sh search "开心"
./run.sh search "开新"
./run.sh search "有点难过" --min-score 0.80
./run.sh search "猫耳" --stable
```
