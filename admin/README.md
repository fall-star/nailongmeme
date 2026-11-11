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

点击“选择文件夹”会打开系统目录选择器，也可以手动输入本机绝对路径。管理端递归扫描：

- 只处理 JPG / PNG / GIF / WebP / AVIF / HEIC / TIFF 等图片扩展名
- txt、json、压缩包等非图片文件直接跳过，不进入扫描结果
- 自动计算 SHA-256，重复图片不会重复入库
- 损坏的图片会标记为 invalid
- 可以批量加标签
- 点击导入后分配新 ID
- 每个候选右侧有“删除”按钮；删除后会从扫描任务和持久化任务中移除，不再参与提交、预览或后续导入

当前原生文件夹选择器使用 macOS `osascript`。手动输入路径在本地其他系统同样可用。

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

如果公开 API 以 `MEME_DEV_RELOAD=1` 启动，catalog 修改后下一次请求会自动重新加载。管理端的“刷新搜索”按钮会主动请求本地 API，触发 SearchEngine 重载。

`data/search/aliases.json` 是人工审阅过的同义词表，`pinyin.json` 是静态字表；管理端不会自动覆盖这两个文件。标签和精确名称搜索会随 catalog 重载立即更新。

前端静态详情页仍来自 `frontend/src/data/gallery.json`。导入后点击管理端的：

```text
同步前台数据
```

或手动运行：

```bash
cd frontend
pnpm sync-data
```

## 线上投稿与 tag 审核

公开站点提供：

```text
主页搜索下方：我也要投稿
详情页标签下方：我要打tag
```

两者都只写入生产隔离目录，不会直接改 catalog：

```text
data/submissions/<submission_id>/
data/tag-suggestions/<suggestion_id>.json
```

管理端“投稿”页点击“拉取线上投稿”，通过 SSH 把新记录拉到本地。然后：

- 图片投稿：填写最终名称/标签，通过后进入本地 catalog
- tag 建议：通过后合并到已有图片标签
- 拒绝：只标记建议，不改 catalog
- 最后点击“同步到服务器”才会修改生产

## 同步到服务器

管理端右上角的“同步到服务器”会先请求预览，再使用本地 SSH 发布到生产：

```text
GET  /api/admin/sync/preview
POST /api/admin/sync
```

默认目标：

```text
root@47.118.50.113
/home/nailong/api
/home/nailong/frontend
https://nailong.fall-star.top
```

可通过环境变量覆盖：

```bash
MEME_PROD_SSH=root@47.118.50.113
MEME_PROD_API_ROOT=/home/nailong/api
MEME_PROD_FRONTEND_ROOT=/home/nailong/frontend
MEME_PROD_BASE_URL=https://nailong.fall-star.top
```

同步内容：

- catalog / memes / state / manifest
- 新增 public 图片的原图、转换图、320/640 缩略图
- 完整重建后的 `frontend/dist`
- 生产 API 重启

安全边界：

- 只上传 `published` 图片
- `draft` 只同步元数据，不上传图片
- 生产 API 1.2 会过滤 draft/blocked
- 上传前要求浏览器确认
- 生产 API/前端目录会先备份
- 需要本机已配置 SSH key，并且 `scp` / `ssh` 可用

如果生产不是 1.2，按钮会拒绝同步并提示先部署 API。

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
