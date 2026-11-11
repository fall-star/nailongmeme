"""Local admin -> production release publisher.

The admin server runs on the operator's machine and uses SSH for the final
write.  It never exposes a public write endpoint.  A preview must be requested
before the actual publish; the UI asks for confirmation.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from meme_pipeline.catalog import load_catalog
from meme_pipeline.config import Config

DEFAULT_PROD_SSH = "root@47.118.50.113"
DEFAULT_PROD_API_ROOT = "/home/nailong/api"
DEFAULT_PROD_FRONTEND_ROOT = "/home/nailong/frontend"
DEFAULT_PROD_BASE_URL = "https://nailong.fall-star.top"
DEFAULT_LOCAL_API = "http://127.0.0.1:8000"
SSH_RE = re.compile(r"^[A-Za-z0-9_.-]+@[A-Za-z0-9.:-]+$")


def _env(name: str, default: str) -> str:
    return os.getenv(name, default).strip() or default


def _prod_ssh() -> str:
    value = _env("MEME_PROD_SSH", DEFAULT_PROD_SSH)
    if not SSH_RE.fullmatch(value):
        raise ValueError(f"invalid MEME_PROD_SSH: {value!r}")
    return value


def _paths() -> tuple[str, str, str, str]:
    api_root = _env("MEME_PROD_API_ROOT", DEFAULT_PROD_API_ROOT).rstrip("/")
    frontend_root = _env("MEME_PROD_FRONTEND_ROOT", DEFAULT_PROD_FRONTEND_ROOT).rstrip("/")
    base = _env("MEME_PROD_BASE_URL", DEFAULT_PROD_BASE_URL).rstrip("/")
    local = _env("MEME_LOCAL_API", DEFAULT_LOCAL_API).rstrip("/")
    for path in (api_root, frontend_root):
        if not path.startswith("/") or " " in path:
            raise ValueError(f"invalid production path: {path!r}")
    return api_root, frontend_root, base, local


def check_ssh() -> dict[str, Any]:
    host = _prod_ssh()
    try:
        proc = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", host, "true"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": str(exc)}
    if proc.returncode != 0:
        return {"ok": False, "error": (proc.stderr or proc.stdout or "ssh failed").strip()[-1000:]}
    return {"ok": True, "error": None}


def _read_json(url: str, timeout: int = 20) -> Any:
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "nailong-admin"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _head_ok(url: str, timeout: int = 10) -> bool:
    request = urllib.request.Request(url, headers={"User-Agent": "nailong-admin"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return 200 <= response.status < 400
    except (OSError, urllib.error.URLError):
        return False


def _review_blocked(cfg: Config) -> tuple[set[str], set[str]]:
    path = cfg.root / "data" / "review" / "sensitive.json"
    blocked_ids: set[str] = set()
    blocked_tags: set[str] = set()
    if not path.is_file():
        return blocked_ids, blocked_tags
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        blocked_ids = {str(value) for value in data.get("blocked_ids", [])}
        blocked_tags = {str(value) for value in data.get("blocked_tags", [])}
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    return blocked_ids, blocked_tags


def preview(cfg: Config) -> dict[str, Any]:
    ssh_host = _prod_ssh()
    api_root, frontend_root, base, local = _paths()
    try:
        health = _read_json(f"{base}/api/health")
        prod_index = _read_json(f"{base}/api/memes.public.json")
    except (OSError, ValueError, urllib.error.URLError) as exc:
        raise RuntimeError(f"生产环境不可达：{exc}") from exc
    version = str(health.get("version", ""))
    if tuple(int(part) for part in version.split(".")[:2]) < (1, 2):
        raise RuntimeError(f"生产 API 版本是 {version}，需要先部署 1.2")
    if not isinstance(prod_index, list):
        raise RuntimeError("生产公开索引格式异常")

    catalog = load_catalog(cfg)
    blocked_ids, blocked_tags = _review_blocked(cfg)
    prod_by_id = {str(item.get("id")): item for item in prod_index if isinstance(item, dict)}
    local_public: list[dict[str, Any]] = []
    draft: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for entry in catalog:
        image_id = str(entry.get("id"))
        status = str(entry.get("status", "published"))
        if status == "draft":
            draft.append(entry)
            continue
        if status == "blocked" or image_id in blocked_ids or any(str(tag) in blocked_tags for tag in entry.get("tag", [])):
            blocked.append(entry)
            continue
        local_public.append(entry)

    new_ids: list[str] = []
    changed: list[str] = []
    for entry in local_public:
        image_id = str(entry["id"])
        previous = prod_by_id.get(image_id)
        if previous is None:
            new_ids.append(image_id)
            continue
        local_tags = [str(tag) for tag in entry.get("tag", [])]
        if "奶蛙" not in local_tags and "奶龙" not in local_tags:
            local_tags.append("奶龙")
        if str(previous.get("name", "")) != str(entry.get("name", "")) or list(previous.get("tag", [])) != local_tags:
            changed.append(image_id)

    local_health = None
    local_error = None
    try:
        local_health = _read_json(f"{local}/api/health", timeout=5)
    except (OSError, ValueError, urllib.error.URLError) as exc:
        local_error = str(exc)

    ssh_check = check_ssh()
    return {
        "ssh": ssh_host,
        "ssh_ok": ssh_check["ok"],
        "ssh_error": ssh_check["error"],
        "api_root": api_root,
        "frontend_root": frontend_root,
        "production_base": base,
        "local_api": local,
        "production_version": version,
        "production_public": len(prod_by_id),
        "local_total": len(catalog),
        "local_public": len(local_public),
        "drafts": len(draft),
        "blocked": len(blocked),
        "new_public_ids": new_ids,
        "changed_public_ids": changed,
        "new_public_count": len(new_ids),
        "changed_public_count": len(changed),
        "local_api_ok": local_health is not None,
        "local_api_error": local_error,
        "frontend_build_required": True,
        "note": "只上传 published 图片；draft 图片不会上传，draft/blocked 元数据会保留并由 API 1.2 过滤。",
    }


def _run(command: list[str], *, cwd: Path, timeout: int = 300) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    # Astro's telemetry config lives under ~/Library/Preferences/astro on
    # macOS.  The local admin process may run under a restricted sandbox, so
    # disable telemetry before invoking the build.
    env["ASTRO_TELEMETRY_DISABLED"] = "1"
    return subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        check=False,
    )


def _copy_metadata(cfg: Config, staging: Path) -> list[str]:
    copied: list[str] = []
    sources = {
        "data/catalog.json": staging / "api/data/catalog.json",
        "memes.json": staging / "api/memes.json",
        "data/state.json": staging / "api/data/state.json",
        "data/manifest.json": staging / "api/data/manifest.json",
        "img/thumbs/manifest.json": staging / "api/img/thumbs/manifest.json",
    }
    for relative, target in sources.items():
        source = cfg.root / relative
        if not source.is_file():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied.append(relative)
    return copied


def _copy_image(cfg: Config, entry: dict[str, Any], staging: Path) -> list[str]:
    image_id = str(entry["id"])
    copied: list[str] = []
    candidates = [
        (cfg.root / str(entry.get("file", "")), str(entry.get("file", ""))),
        (cfg.root / "img" / "converted" / f"{image_id}.jpg", f"img/converted/{image_id}.jpg"),
        (cfg.root / "img" / "converted" / f"{image_id}.gif", f"img/converted/{image_id}.gif"),
        (cfg.root / "img" / "thumbs" / "320" / f"{image_id}.webp", f"img/thumbs/320/{image_id}.webp"),
        (cfg.root / "img" / "thumbs" / "640" / f"{image_id}.webp", f"img/thumbs/640/{image_id}.webp"),
    ]
    for source, relative in candidates:
        if not source.is_file():
            continue
        target = staging / "api" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied.append(relative)
    if not any(path.startswith("img/converted/") for path in copied):
        raise RuntimeError(f"ID {image_id} 缺少转换后的图片")
    return copied


def sync(cfg: Config) -> dict[str, Any]:
    summary = preview(cfg)
    if not summary["local_api_ok"]:
        raise RuntimeError(f"本地 API 不可用，无法构建前台：{summary['local_api_error']}")
    if not summary.get("ssh_ok"):
        raise RuntimeError(f"无法通过 SSH 连接生产：{summary.get('ssh_error')}")
    ssh_host = summary["ssh"]
    api_root = summary["api_root"]
    frontend_root = summary["frontend_root"]
    base = summary["production_base"]

    pnpm = shutil.which("pnpm")
    if not pnpm:
        raise RuntimeError("找不到 pnpm，无法刷新和构建前台")

    # The local API must see the latest catalog before sync-data.
    try:
        _read_json(f"{summary['local_api']}/api/health", timeout=8)
    except (OSError, ValueError, urllib.error.URLError) as exc:
        raise RuntimeError(f"本地 API 刷新失败：{exc}") from exc
    sync_data = _run([pnpm, "--dir", "frontend", "sync-data"], cwd=cfg.root, timeout=180)
    if sync_data.returncode != 0:
        raise RuntimeError((sync_data.stderr or sync_data.stdout or "sync-data failed")[-3000:])
    build = _run([pnpm, "--dir", "frontend", "build"], cwd=cfg.root, timeout=300)
    if build.returncode != 0:
        raise RuntimeError((build.stderr or build.stdout or "frontend build failed")[-3000:])

    catalog = load_catalog(cfg)
    by_id = {str(entry["id"]): entry for entry in catalog}
    staging = cfg.root / "data" / "admin" / "releases" / uuid.uuid4().hex
    staging.mkdir(parents=True, exist_ok=True)
    copied_meta = _copy_metadata(cfg, staging)
    copied_images: list[str] = []
    for image_id in summary["new_public_ids"]:
        entry = by_id[image_id]
        copied_images.extend(_copy_image(cfg, entry, staging))
    frontend_source = cfg.root / "frontend" / "dist"
    if not (frontend_source / "index.html").is_file():
        raise RuntimeError("frontend/dist 不完整")
    shutil.copytree(frontend_source, staging / "frontend", dirs_exist_ok=True)

    tar_path = Path(tempfile.gettempdir()) / f"nailong-sync-{uuid.uuid4().hex}.tar"
    with tarfile.open(tar_path, "w") as archive:
        archive.add(staging, arcname=".")
    remote_tmp = f"/tmp/nailong-sync-{uuid.uuid4().hex}"
    remote_tar = f"{remote_tmp}.tar"
    try:
        scp = subprocess.run(
            ["scp", "-q", str(tar_path), f"{ssh_host}:{remote_tar}"],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if scp.returncode != 0:
            raise RuntimeError((scp.stderr or scp.stdout or "scp failed")[-3000:])
        remote_script = f'''set -e
API={shlex.quote(api_root)}
FE={shlex.quote(frontend_root)}
TMP={shlex.quote(remote_tmp)}
TAR={shlex.quote(remote_tar)}
TS=$(date +%Y%m%dT%H%M%S)
mkdir -p "$API/data" "$API/img" "$TMP"
tar -C "$TMP" -xf "$TAR"
cp -a "$API/data/catalog.json" "$API/data/catalog.json.bak-$TS" 2>/dev/null || true
cp -a "$API/memes.json" "$API/memes.json.bak-$TS" 2>/dev/null || true
cp -a "$API/data/state.json" "$API/data/state.json.bak-$TS" 2>/dev/null || true
if [ -d "$TMP/api/img" ]; then
  mkdir -p "$API/img/raw" "$API/img/converted" "$API/img/thumbs/320" "$API/img/thumbs/640"
  cp -a "$TMP/api/img/." "$API/img/"
fi
cp -a "$TMP/api/data/catalog.json" "$API/data/catalog.json"
if [ -f "$TMP/api/memes.json" ]; then cp -a "$TMP/api/memes.json" "$API/memes.json"; fi
if [ -f "$TMP/api/data/state.json" ]; then cp -a "$TMP/api/data/state.json" "$API/data/state.json"; fi
if [ -f "$TMP/api/data/manifest.json" ]; then cp -a "$TMP/api/data/manifest.json" "$API/data/manifest.json"; fi
if [ -f "$TMP/api/img/thumbs/manifest.json" ]; then cp -a "$TMP/api/img/thumbs/manifest.json" "$API/img/thumbs/manifest.json"; fi
chown -R nailong:nailong "$API/data" "$API/memes.json" "$API/img"
systemctl restart nailong-api
for i in $(seq 1 20); do
  if curl -fsS http://127.0.0.1:8000/api/health >/dev/null 2>&1; then break; fi
  sleep 0.5
done
curl -fsS http://127.0.0.1:8000/api/health >/dev/null
if [ -d "$FE" ]; then mv "$FE" "$FE.bak-$TS"; fi
mv "$TMP/frontend" "$FE"
chmod 755 "$FE"
chown -R nailong:nailong "$FE"
rm -rf "$TMP" "$TAR"
echo "deployed:$TS"
'''
        ssh = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", ssh_host, "bash -lc " + shlex.quote(remote_script)],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if ssh.returncode != 0:
            raise RuntimeError((ssh.stderr or ssh.stdout or "remote deploy failed")[-4000:])
    finally:
        tar_path.unlink(missing_ok=True)
        shutil.rmtree(staging, ignore_errors=True)

    try:
        final_health = _read_json(f"{base}/api/health")
    except (OSError, ValueError, urllib.error.URLError) as exc:
        raise RuntimeError(f"部署命令完成，但生产健康检查失败：{exc}") from exc
    missing = [image_id for image_id in summary["new_public_ids"] if not _head_ok(f"{base}/api/images/{image_id}")]
    if missing:
        raise RuntimeError(f"部署后图片检查失败：{', '.join(missing[:10])}")
    return {
        "ok": True,
        "production_version": final_health.get("version"),
        "public_images": final_health.get("public_images"),
        "new_public_ids": summary["new_public_ids"],
        "changed_public_count": summary["changed_public_count"],
        "copied_images": len(copied_images),
        "metadata": copied_meta,
        "remote": {"host": ssh_host, "api_root": api_root, "frontend_root": frontend_root},
    }
