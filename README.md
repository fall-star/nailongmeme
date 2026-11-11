# Nailong Meme Library

[English](README.md) | [简体中文](README.zh-CN.md)

A self-hosted meme library for Nailong and Naiwa images: a FastAPI API with public read endpoints, authenticated quarantine writers, and an Astro static frontend.


## Features

- Keyword search with Chinese homophone tolerance, plus compound tag search such as `奶蛙大笑`
- Gallery with 320/640 WebP thumbnails, tag filtering and GIF/JPG badges
- Pre-rendered detail pages with single-image download
- Category pages for 奶蛙 / 奶龙 / 情绪 / 动作 / 标签
- Random filmstrip on the home page; cards link to detail pages
- Optional sprite-sheet desk pet (asset not included, see below)
- Sensitive-content filter driven by `data/review/sensitive.json`
- SQLite-backed accounts and sessions, separate from the image catalog
- Email/password registration and admin email-code login
- Sign-in-gated meme submissions and per-image tag suggestions, held in quarantine until local admin review and sync

## Repository layout

```text
meme_pipeline/          crawler, catalog, search and verification code
production/api/         FastAPI application (source included)
tools/                  thumbnail and search-table builders
schemas/                JSON schemas
tests/                  unittest suite (fixtures, no bundled images)
data/                   catalog, memes index and search tables (metadata only)
frontend/               Astro + React islands frontend
admin/                  local-only management UI and admin API
docs/                   public API and deployment notes
run.sh                  project CLI wrapper
convert_formats.py      JPG/GIF conversion tool
```

## What is not included

Image files and third-party visual assets are intentionally not bundled:

- `img/raw/`, `img/converted/`, `img/thumbs/`
- `design/` screenshots and mockups
- `reports/` historical crawl reports
- `frontend/public/mascot/naifrog-sheet.webp` — the desk-pet sprite is a development reference from [nai-hub](https://nai-hub.com); confirm permission or replace it with an authorized asset before publishing.
- `production/` deployment data and server configuration
- internal `AI_CONTEXT.md` / `CRAWL_SPEC.md`

The repository includes metadata, so the catalog/search code and frontend build can be developed without distributing the image set. The API will return an empty gallery until image files are supplied.

Expected image layout:

```text
img/raw/<id>.<original-extension>
img/converted/<id>.jpg    # static image
img/converted/<id>.gif    # animated image
img/thumbs/320/<id>.webp
img/thumbs/640/<id>.webp
```

## Quick start

Requirements: Python 3.12, [uv](https://docs.astral.sh/uv/), Node.js 20+ and pnpm.

```bash
git clone https://github.com/fall-star/nailongmeme.git
cd nailongmeme

uv sync --extra api
./run.sh verify
```

`verify` needs the image files described above. Unit tests that do not need the real image set:

```bash
.venv/bin/python -m unittest discover -s tests -q
```

Build thumbnails after adding `img/converted/`:

```bash
./run.sh thumbs
```

Start the API:

```bash
MEME_DATA_DIR="$PWD" \
MEME_PUBLIC_BASE_URL="http://127.0.0.1:8000" \
MEME_CORS_ORIGINS="*" \
uv run --extra api uvicorn production.api.main:app --host 127.0.0.1 --port 8000
```

Start the frontend in another terminal:

```bash
cd frontend
pnpm install
pnpm dev
# http://127.0.0.1:4321
```

The Astro dev server proxies `/api` to `http://127.0.0.1:8000`. For a static build:

```bash
cd frontend
pnpm build
# output: frontend/dist/
```

Frontend detail pages are generated from `frontend/src/data/gallery.json`. Refresh that snapshot after catalog changes:

```bash
# API must be running on 127.0.0.1:8000
cd frontend
pnpm sync-data
```

## API

Base URL in development: `http://127.0.0.1:8000/api`

| Endpoint | Description |
|---|---|
| `GET /api/search?q=` | Return one matched image as JPG/GIF bytes |
| `GET /api/search/meta?q=` | Search metadata JSON |
| `GET /api/search/list?q=` | Ranked multi-result search |
| `GET /api/random` | Random image bytes |
| `GET /api/random/meta` | Random image metadata |
| `GET /api/images/{id}` | Image bytes; `?download=1` returns attachment |
| `GET /api/gallery` | Paginated public gallery metadata |
| `GET /api/thumbs/{id}/320` | WebP card thumbnail |
| `GET /api/thumbs/{id}/640` | Larger WebP card thumbnail |
| `GET /api/memes.json` | Full metadata index (attachment) |
| `GET /api/memes.public.json` | Public metadata index |
| `GET /api/health` | Service and catalog status |

Image endpoints return image bytes only. Use the `meta` or `gallery` endpoints for JSON.

See [docs/API.md](docs/API.md) for parameters and response examples. When the frontend is running, the readable Chinese API guide is at `/api-docs`.

OpenAPI 3.1 specification: [docs/openapi-ip.yaml](docs/openapi-ip.yaml) ([JSON](docs/openapi-ip.json)).
It uses `https://nailong.fall-star.top` as the server and can be imported into Swagger UI, Postman or ReDoc.

### Tag rule

Any catalog entry without a `奶蛙` tag is treated as `奶龙` at runtime. The source catalog is not rewritten. This rule is applied consistently in search, gallery, random selection and the metadata APIs.

### Sensitive-content filter

`data/review/sensitive.json` is a conservative, editable first-pass blocklist. It hides matching entries from public gallery, search, random selection and image/thumbnail routes. The current status is `pending_user_review`; it is not a substitute for human content review.

## Data and rights

The image names, tags and source URLs in `data/catalog.json` are included for development. Image files, the mascot sprite and screenshots remain the property of their respective owners and are not part of the MIT-licensed source distribution.

The MIT license applies to the code in this repository, not to third-party images or character IP.

## Local admin (development only)

The admin tool is a separate localhost app. It is not part of the production frontend and must not be run on the public server.

```bash
# API with dev reload
MEME_DATA_DIR="$PWD" MEME_DEV_RELOAD=1 \
  uv run --extra api uvicorn production.api.main:app --host 127.0.0.1 --port 8000

# Local admin UI and API
MEME_DATA_DIR="$PWD" ./run.sh admin
# http://127.0.0.1:8001/admin
```

It supports single-image upload, native folder selection, image-only folder scanning, visual name/tag editing (including existing images), draft/published status, never-reused IDs, thumbnails, per-candidate deletion and a quarantined submission inbox. The “同步到服务器” button previews, confirms, builds and deploys the selected changes over SSH. See [admin/README.md](admin/README.md).

## Deployment

A native deployment uses systemd, Nginx and uv. Keep the API and frontend in separate directories and serve the static frontend directly:

```text
/home/nailong/api/        API code, data and virtualenv
/home/nailong/frontend/   frontend/dist contents
```

See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for a minimal setup. No production credentials are included.

## License

[MIT](LICENSE)
