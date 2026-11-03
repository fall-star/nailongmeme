# 本地管理端

只在本机开发环境运行的管理页面，不属于生产前端，也不会由生产 API 加载。

## 启动

先启动本地公开 API（开发时建议打开自动重载）：

```bash
MEME_DATA_DIR="$PWD" \
MEME_DEV_RELOAD=1 \
MEME_PUBLIC_BASE_URL="http://127.0.0.1:8000" \
MEME_CORS_ORIGINS="*" \
.venv/bin/python -m uvicorn production.api.main:app --host 127.0.0.1 --port 8000
```

再启动本地管理端：

```bash
MEME_DATA_DIR="$PWD" ./run.sh admin
```

打开：

```text
http://127.0.0.1:8001/admin
```

管理端只监听 `127.0.0.1`，没有生产入口，也不需要把 `admin/` 打包进前端。

## 图片入库

### 单张

选择一张图片，确认名称和标签后导入。默认状态是 `draft`，不会出现在公开 Gallery/Search/Random。

### 文件夹

输入本机文件夹绝对路径，管理端递归扫描：

- 支持 JPG / PNG / GIF / WebP 等常见格式
- 自动计算 SHA-256，重复图片不会重复入库
- 损坏或不支持的图片会标记为 invalid
- 可以批量加标签
- 点击导入后分配新 ID

### ID

- 只在点击“导入选中项”时分配
- 使用现有 `data/state.json` 的 `next_id`
- 同时避开 catalog、`img/raw`、`img/converted` 中已占用的 ID
- 写入前先推进高水位，失败也不会复用 ID
- 新 ID 不会复用旧空洞

### 入库后的文件

```text
img/raw/<id>.<ext>           原始文件
img/converted/<id>.jpg|gif   公开图片
img/thumbs/320/<id>.webp     卡片缩略图
img/thumbs/640/<id>.webp     大图缩略图
data/manifest.json           来源与 dhash 元数据
img/thumbs/manifest.json     缩略图尺寸与大小索引
```

导入成功后同时写入来源 manifest 和缩略图 manifest，避免 `verify` 产生缺失 provenance 的警告。

默认 `status: draft`。在“素材库”里补名称和标签后，把状态改为 `published`。

## 旧图编辑

素材库支持：

- 按 ID、名称、标签搜索
- 筛选状态 / 无标签
- 大图预览
- 修改名称、标签、发布状态
- 不改变 ID，不移动文件

保存后会写 catalog、`memes.json`、`data/state.json` 和本地审计日志。

## 导入任务恢复

扫描结果会写到：

```text
data/admin/imports/<scan_id>.json
```

管理端重启后可以在“导入”页恢复未完成扫描。已完整提交的扫描会自动删除。

## 导入后刷新

如果公开 API 以 `MEME_DEV_RELOAD=1` 启动，catalog 修改后下一次请求会自动重新加载。

前端静态详情页仍来自 `frontend/src/data/gallery.json`。导入后点击管理端的：

```text
同步前台数据
```

或手动运行：

```bash
cd frontend
pnpm sync-data
```

## 生成生产发布包

完整发布包可以先生成到本地，不直接接触服务器：

```bash
python3 tools/prepare_release.py \
  --ids 1182,1185-1187 \
  --with-frontend \
  --output release/20261104
```

发布包包含 API 代码、catalog/memes/state、manifest、选中图片和可选前端构建。确认 `release/release.json` 后再按部署文档上传。API 代码必须先于含 `draft` 的 catalog 上线。

## 投稿（后端基础）

`admin/server.py` 提供了本地投稿原型：

```text
POST /api/submissions
GET  /api/admin/submissions
POST /api/admin/submissions/{id}/approve
POST /api/admin/submissions/{id}/reject
```

投稿图片进入：

```text
data/submissions/<submission_id>/
```

只有审核通过后才会分配 catalog ID、写入 catalog 并公开。当前公开投稿前端尚未接入，生产 API 也还没有开放这个写入入口。

## 安全边界

- 管理端只绑定 `127.0.0.1`
- 生产 Nginx 没有 `/admin`，生产 API 没有 `/api/admin/*`
- `data/submissions`、`data/admin` 已加入 `.gitignore`
- 管理端不做多用户权限，仅适合单机开发使用
