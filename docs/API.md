# API

Development base URL: `http://127.0.0.1:8000/api`

Production OpenAPI document: [openapi-ip.yaml](openapi-ip.yaml) · [JSON](openapi-ip.json) · server `https://nailong.fall-star.top`

Read endpoints are public. `POST /api/submissions` and `POST /api/tag-suggestions` require a signed-in account and a session cookie.

All successful image endpoints return image bytes, not JSON. Use the metadata endpoints when `id`, `name`, `tag`, `image_url` or `thumb_url` are needed.

## Auth

Account writes use a secure, host-only session cookie. Read endpoints remain public.

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/auth/register` | Create an account |
| POST | `/api/auth/login` | Password login; admin accounts receive an email-code challenge |
| POST | `/api/auth/verify-code` | Complete an admin email-code challenge |
| POST | `/api/auth/resend-code` | Resend an admin challenge code |
| POST | `/api/auth/logout` | Delete the current session |
| GET | `/api/auth/me` | Current signed-in user |
| POST | `/api/account/avatar` | Upload or replace the current avatar |
| GET | `/api/avatars/default` | Default avatar |
| GET | `/api/avatars/{user_id}` | User avatar |

`POST /api/submissions` and `POST /api/tag-suggestions` return `401` without a valid session.

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
  "version": "1.4.0",
  "catalog_entries": 1171,
  "images": 1171,
  "public_images": 1164,
  "hidden_images": 7,
  "review_status": "pending_user_review",
  "memes_count": 1171,
  "search_ready": true
}
```


## Public write endpoints

These endpoints only write to quarantined inboxes. They never assign a catalog id, mutate the catalog, or return a public image URL.

### `POST /api/submissions`

Raw image body. Query parameters:

- `filename` — original filename
- `name` — optional suggested name
- `tag` — optional comma-separated tags
- `source` — optional provenance
- `contact` — optional private contact
- `rights_accepted=true` — required

Returns:

```json
{"submission_id": "...", "status": "pending_review"}
```

Maximum body size: 25 MB. Duplicate content returns `status: "duplicate"`.

### `POST /api/tag-suggestions`

```json
{
  "image_id": "4",
  "tags": ["大笑", "上班"],
  "note": "这个表情更像大笑"
}
```

Returns:

```json
{"suggestion_id": "...", "status": "pending_review"}
```

Suggestions are reviewed in the local admin app. Only an approved suggestion updates the local catalog; the public image changes only after the admin clicks “同步到服务器”.
