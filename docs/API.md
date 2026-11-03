# API

Development base URL: `http://127.0.0.1:8000/api`

Production IP OpenAPI document: [openapi-ip.yaml](openapi-ip.yaml) · [JSON](openapi-ip.json) · server `http://47.118.50.113`

All successful image endpoints return image bytes, not JSON. Use the metadata endpoints when `id`, `name`, `tag`, `image_url` or `thumb_url` are needed.

## Search

### `GET /api/search?q=开心`

Returns one matched JPG/GIF image.

Parameters:

- `q` — required, 1–64 characters
- `min_score` — default `0.80`
- `window` — default `0.04`
- `seed` — optional
- `stable` — return the first result in the top group

If nothing reaches `min_score`, a random image is returned.

### `GET /api/search/meta?q=开心`

Same search, returned as metadata JSON, including `method`, `score`, `fallback` and `candidate_count`.

### `GET /api/search/list?q=开心&limit=40&offset=0`

Ranked multi-result search. No random fallback is applied: a query with no match returns `fallback: true` and an empty `items` array.

## Random

- `GET /api/random` — random image bytes
- `GET /api/random/meta` — random image metadata
- `GET /api/random/image` — alias of `/api/random`

## Gallery

### `GET /api/gallery`

Parameters:

- `q` — local name/tag substring
- `tag` — exact tag
- `tags` — comma-separated tags; all must match, e.g. `tags=奶蛙,大笑`
- `format` — `jpeg` or `gif`
- `limit` — 1–100, default 48
- `offset` — default 0

Response:

```json
{
  "total": 1164,
  "limit": 48,
  "offset": 0,
  "items": [
    {
      "id": "4",
      "name": "乖巧待机",
      "tag": ["奶蛙", "开心"],
      "format": "jpeg",
      "width": 134,
      "height": 132,
      "bytes": 7309,
      "image_url": "http://127.0.0.1:8000/api/images/4",
      "image_download_url": "http://127.0.0.1:8000/api/images/4?download=1",
      "thumb_url": "http://127.0.0.1:8000/api/thumbs/4/640",
      "thumb_320_url": "http://127.0.0.1:8000/api/thumbs/4/320",
      "thumb_640_url": "http://127.0.0.1:8000/api/thumbs/4/640"
    }
  ]
}
```

### `GET /api/gallery/{id}`

One public gallery item.

### `GET /api/thumbs/{id}/320` and `/640`

WebP thumbnails. GIFs use the first frame. Originals remain at `/api/images/{id}`.

## Images

### `GET /api/images/{id}`

Returns the JPG/GIF image. `?download=1` adds an attachment disposition.

Image responses use long-lived immutable caching; search/random responses use `no-store`.

## Metadata indexes

- `GET /api/memes.json` — complete 1171-entry index as an attachment
- `GET /api/memes.public.json` — public 1164-entry index

Entries without `奶蛙` receive a derived `奶龙` tag at runtime.

## Health

`GET /api/health`

```json
{
  "status": "ok",
  "version": "1.2.0",
  "catalog_entries": 1171,
  "images": 1171,
  "public_images": 1164,
  "hidden_images": 7,
  "review_status": "pending_user_review",
  "memes_count": 1171,
  "search_ready": true
}
```
