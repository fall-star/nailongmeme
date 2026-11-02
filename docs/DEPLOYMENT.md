# Deployment

This is a minimal native deployment using systemd, Nginx and uv. It assumes a Linux server with Python 3.12 and Node.js/pnpm available.

## Layout

Keep API runtime files outside the public frontend root:

```text
/home/nailong/api/        API code, data, thumbnails and .venv
/home/nailong/frontend/   contents of frontend/dist
```

## API

```bash
cd /home/nailong/api
uv sync --extra api
```

Environment (`/etc/nailong/api.env`):

```env
MEME_DATA_DIR=/home/nailong/api
MEME_PUBLIC_BASE_URL=https://example.com
MEME_CORS_ORIGINS=https://example.com
```

Required data:

```text
memes.json
data/catalog.json
data/search/aliases.json
data/search/pinyin.json
data/review/sensitive.json
img/converted/<id>.jpg|gif
img/thumbs/320/<id>.webp
img/thumbs/640/<id>.webp
```

Build thumbnails locally:

```bash
./run.sh thumbs
```

Systemd example:

```ini
[Unit]
Description=Meme library API
After=network.target

[Service]
User=nailong
Group=nailong
WorkingDirectory=/home/nailong/api
EnvironmentFile=/etc/nailong/api.env
ExecStart=/home/nailong/api/.venv/bin/uvicorn production.api.main:app --host 127.0.0.1 --port 8000 --workers 2 --proxy-headers --forwarded-allow-ips=127.0.0.1
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

## Frontend

```bash
cd frontend
pnpm install
pnpm build
```

Copy `frontend/dist/` contents to `/home/nailong/frontend/`.

Nginx root must point at the frontend directory, not the API parent:

```nginx
root /home/nailong/frontend;
index index.html;

location ^~ /api/ {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
}

location / {
    try_files $uri/index.html $uri /index.html;
}
```

Do not deploy with a command that deletes `/home/nailong`; use a separate frontend release directory or exclude `api/`.

## HTTPS

Terminate TLS at Nginx or a reverse proxy. Certificates are not included. Complete any required domain/ICP process before enabling public TLS.
