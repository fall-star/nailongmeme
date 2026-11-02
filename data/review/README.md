# 敏感内容审查配置

`data/review/sensitive.json` 是公开 API 的第一版过滤配置：

- `blocked_tags`：命中任一 exact tag 的图片不进入 gallery/search/random；
- `blocked_ids`：显式保护，即使后续改 tag 也不暴露；
- `review_status=pending_user_review`：当前只是保守初筛，不是最终内容审查结论。

公开 API 不返回被过滤条目的图片、缩略图、元数据或搜索结果；本地 catalog 和原始图片
仍完整保留，方便后续人工复审和恢复。
