# 奶龙表情库前端（开发中）

Astro 静态站 + React islands + TypeScript。当前只在本地开发，**未部署生产**。

## 本地运行

先启动本地 API（仓库根目录）：

```bash
MEME_DATA_DIR="$PWD" \
MEME_PUBLIC_BASE_URL="http://127.0.0.1:8000" \
.venv/bin/python -m uvicorn production.api.main:app --host 127.0.0.1 --port 8000
```

再启动前端：

```bash
cd frontend
pnpm install
pnpm dev
# http://127.0.0.1:4321
```

Vite dev proxy 会把 `/api` 转发到 `http://127.0.0.1:8000`，所以浏览器只使用同源 `/api`。

## 构建

```bash
cd frontend
pnpm build
# dist/ 静态输出；详情页由 src/data/gallery.json 生成
```

数据快照更新：

```bash
# 本地 API 需要运行
pnpm sync-data
```

## 结构

```text
src/pages/index.astro       首页 + GalleryApp 岛
src/pages/meme/[id].astro   1164 个静态详情页
src/pages/tags/index.astro  标签页
src/components/GalleryApp.tsx  图库、筛选、智能搜索、灯箱
src/data/gallery.json       从 /api/gallery 同步的公开快照
src/styles/global.css       设计系统
```

## 页面与交互

- `/`：Hero + 随机胶片滚筒 + 全量图库（ID 倒序）；
- `/naiwa`、`/nailong`：对应角色结果直接在本页；
- `/mood`、`/actions`：只显示对应类别标签，标签点击留在本页；
- `/tags`：全部标签，点击后留在本页筛选；
- `/meme/{id}`：静态详情页；
- `/api-docs`：中文 API 文档；头部提供 GitHub 仓库入口；
- 搜索：中文同音字 + 复合标签（`奶蛙大笑`）；
- 首页右下角奶蛙桌宠：使用 nai-hub 同款精灵表动画，点击播放 cheer（鼓个掌）动作，同时气泡随机展示一张表情；
- 桌宠空闲会在 idle / wait / review / sleep 行中随机切换，约 24 秒未点击会躲到底边只露头；
- 首页随机胶片滚筒的卡片可直接点击进入详情页；
- 单张下载、灯箱、复制链接。

## 已接入

- `/api/gallery` 分页元数据；
- `/api/search/list` 多结果搜索（中文同音字）；
- `/api/thumbs/...` 卡片缩略图；
- `/api/images/{id}?download=1` 单张下载；
- 桌宠精灵表：`public/mascot/naifrog-sheet.webp` + `src/data/pet.json`（开发参考素材，上线前确认授权）；
- 7 条敏感/待审内容从公开数据过滤；
- 首页全量图库按 ID 倒序，另提供名称排序；分类/标签页筛选后仍留在本页。

## 尚待完成

- 生产部署（必须先把 API、缩略图、审查配置同步到服务器）；
- 前端/API 目录分离的 Nginx 切换；
- 更多内容审查、SEO sitemap、移动端交互细化。
