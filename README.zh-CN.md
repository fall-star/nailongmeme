# 奶龙表情库

一个自托管的奶龙 / 奶蛙表情包项目，由只读 FastAPI 接口和 Astro 静态前端组成。

[English](README.md) | [简体中文](README.zh-CN.md)

## 功能

- 中文关键词搜索，支持同音错字，例如 `开新 → 开心`
- 复合标签搜索，例如 `奶蛙大笑`
- 图库、标签筛选、GIF / JPG 标识和分页加载
- 静态详情页，支持单张原图下载
- 奶蛙、奶龙、情绪、动作、标签分类页
- 首页随机胶片滚筒，卡片可直接进入详情
- 可选的奶蛙精灵表桌宠
- 公开内容审查过滤，配置在 `data/review/sensitive.json`
- 无数据库、无写入接口

## 项目结构

```text
meme_pipeline/          抓取、目录、搜索和校验代码
production/api/         FastAPI 应用源码
tools/                  缩略图和搜索表生成工具
schemas/                JSON Schema
tests/                  单元测试（使用临时 fixture，不包含真实图片）
data/                   目录、索引和搜索表（仅元数据）
frontend/               Astro + React islands 前端
admin/                  只在本地运行的管理页面和管理 API
docs/                   API 和部署文档
run.sh                  项目命令入口
convert_formats.py      JPG/GIF 转换工具
```

## 仓库不包含的内容

为避免传播第三方图片和过大的文件，仓库**不包含**：

- `img/raw/`、`img/converted/`、`img/thumbs/`
- `design/` 截图和设计稿
- `reports/` 历史抓取报告
- 奶蛙桌宠精灵表 `frontend/public/mascot/naifrog-sheet.webp`
- `production/` 部署数据、Nginx/systemd 配置
- 内部工作文档 `AI_CONTEXT.md`、`CRAWL_SPEC.md`

仓库保留了图片名称、标签和来源等元数据，因此可以先开发和测试目录/搜索逻辑；在补充图片之前，API 图库会为空。

如果你要运行完整站点，请准备以下目录：

```text
img/raw/<id>.<原始扩展名>
img/converted/<id>.jpg    # 静态图
img/converted/<id>.gif    # 动态图
img/thumbs/320/<id>.webp
img/thumbs/640/<id>.webp
```

桌宠精灵表需要自行放入 `frontend/public/mascot/naifrog-sheet.webp`。当前使用的参考素材来自 nai-hub，正式使用前请确认授权，或替换为自有的奶蛙动作素材。

## 快速开始

需要 Python 3.12、[uv](https://docs.astral.sh/uv/)、Node.js 20+ 和 pnpm。

```bash
git clone https://github.com/fall-star/nailongmeme.git
cd nailongmeme

uv sync --extra api
./run.sh verify
```

`./run.sh verify` 需要真实图片目录。没有图片时，可以运行不依赖图片的单元测试：

```bash
.venv/bin/python -m unittest discover -s tests -q
```

放入 `img/converted/` 后生成缩略图：

```bash
./run.sh thumbs
```

启动本地 API：

```bash
MEME_DATA_DIR="$PWD" \
MEME_PUBLIC_BASE_URL="http://127.0.0.1:8000" \
MEME_CORS_ORIGINS="*" \
uv run --extra api uvicorn production.api.main:app --host 127.0.0.1 --port 8000
```

另开终端启动前端：

```bash
cd frontend
pnpm install
pnpm dev
# http://127.0.0.1:4321
```

Astro 开发服务器会把 `/api` 代理到 `http://127.0.0.1:8000`。

构建静态前端：

```bash
cd frontend
pnpm build
# 输出：frontend/dist/
```

详情页由 `frontend/src/data/gallery.json` 生成。目录更新后，在本地 API 运行的情况下刷新快照：

```bash
cd frontend
pnpm sync-data
```

## API

本地基础地址：`http://127.0.0.1:8000/api`

| 接口 | 用途 |
|---|---|
| `GET /api/search?q=` | 搜索并返回一张 JPG/GIF 图片 |
| `GET /api/search/meta?q=` | 搜索元数据 JSON |
| `GET /api/search/list?q=` | 多结果搜索，按相关度排序 |
| `GET /api/random` | 随机 JPG/GIF 图片 |
| `GET /api/random/meta` | 随机图片元数据 |
| `GET /api/images/{id}` | 原图；`?download=1` 返回附件 |
| `GET /api/gallery` | 公开图库元数据，支持分页和筛选 |
| `GET /api/thumbs/{id}/320` | 320px WebP 缩略图 |
| `GET /api/thumbs/{id}/640` | 640px WebP 缩略图 |
| `GET /api/memes.json` | 完整元数据索引（附件） |
| `GET /api/memes.public.json` | 公开元数据索引 |
| `GET /api/health` | 服务状态和目录计数 |

图片接口只返回图片字节，不返回 JSON。需要元数据时使用 `meta`、`gallery` 或索引接口。

完整中文说明见 [docs/API.md](docs/API.md)；前端运行时可以打开 `/api-docs` 查看可读版 API 文档。

OpenAPI 3.1：

- [docs/openapi-ip.yaml](docs/openapi-ip.yaml)
- [docs/openapi-ip.json](docs/openapi-ip.json)
- 服务器地址：`http://47.118.50.113`

可以直接导入 Swagger UI、Postman 或 ReDoc。

### 标签规则

没有 `奶蛙` 标签的记录在运行时会被视为 `奶龙`。源目录文件不会被改写；搜索、图库、随机选择和元数据接口都遵守这条规则。

### 内容审查

`data/review/sensitive.json` 是一份可编辑的初筛名单。命中的条目不会出现在公开图库、搜索、随机结果、图片或缩略图接口中。当前状态为 `pending_user_review`，不能替代人工内容审查。

## 数据与版权

`data/catalog.json` 中的图片名称、标签和来源地址仅用于开发。图片文件、桌宠精灵表和截图的权利归各自所有者，不属于 MIT 授权的代码范围。

MIT 许可证只适用于本仓库中的代码，不适用于第三方图片、角色形象或视觉素材。

## 本地管理端（仅开发）

管理端是独立的本机应用，不进入生产前端构建，也不要在生产服务器运行。

```bash
# 公开 API，开发时开启自动重载
MEME_DATA_DIR="$PWD" MEME_DEV_RELOAD=1 \
  .venv/bin/python -m uvicorn production.api.main:app --host 127.0.0.1 --port 8000

# 本地管理页面 + API
MEME_DATA_DIR="$PWD" ./run.sh admin
# http://127.0.0.1:8001/admin
```

支持单张上传、扫描本地文件夹、可视化修改名称/标签（含旧图）、草稿/发布状态、不复用 ID、缩略图，以及投稿隔离队列。详见 [admin/README.md](admin/README.md)。

## 部署

原生部署使用 systemd、Nginx 和 uv。API 和前端目录分离：

```text
/home/nailong/api/        API 代码、数据、缩略图和虚拟环境
/home/nailong/frontend/   frontend/dist 静态文件
```

Nginx 的 root 指向前端目录，`/api/` 反向代理到 FastAPI。不要用会删除 `/home/nailong` 的同步命令，以免误删 API 目录。

最小部署步骤见 [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)。仓库不包含生产凭据和服务器配置。

## 许可证

[MIT](LICENSE)
