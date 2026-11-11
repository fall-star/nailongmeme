"""Send optional review-notification email through the production mail CLI.

The SMTP password stays on the production host.  The local admin app invokes
the already-deployed ``production.api.send_mail`` module over SSH with a JSON
payload on stdin.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from typing import Any

DEFAULT_PROD_SSH = "root@47.118.50.113"
DEFAULT_PROD_API_ROOT = "/home/nailong/api"
DEFAULT_SITE_URL = "https://nailong.fall-star.top"


def _host() -> str:
    return (os.getenv("MEME_PROD_SSH", DEFAULT_PROD_SSH) or DEFAULT_PROD_SSH).strip()


def _api_root() -> str:
    return (os.getenv("MEME_PROD_API_ROOT", DEFAULT_PROD_API_ROOT) or DEFAULT_PROD_API_ROOT).strip().rstrip("/")


def _site_url() -> str:
    return (os.getenv("MEME_PROD_BASE_URL", DEFAULT_SITE_URL) or DEFAULT_SITE_URL).strip().rstrip("/")


def send(payload: dict[str, Any]) -> dict[str, Any]:
    email = str(payload.get("email", "")).strip()
    if "@" not in email:
        return {"ok": False, "error": "投稿记录没有可用的邮箱"}
    body = {
        "email": email,
        "nickname": str(payload.get("nickname", "") or "投稿者"),
        "title": str(payload.get("title", "") or "审核通知"),
        "body": str(payload.get("body", "") or ""),
        "site_url": _site_url(),
    }
    root = shlex.quote(_api_root())
    command = (
        "set -a; . /etc/nailong/api.env; set +a; "
        f"cd {root} && .venv/bin/python -m production.api.send_mail"
    )
    try:
        proc = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", _host(), "bash -lc " + shlex.quote(command)],
            input=json.dumps(body, ensure_ascii=False),
            capture_output=True,
            text=True,
            timeout=40,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": str(exc)[:500]}
    if proc.returncode != 0:
        return {"ok": False, "error": (proc.stderr or proc.stdout or "mail command failed").strip()[-800:]}
    return {"ok": True, "error": None}


def submission_approved(record: dict[str, Any], catalog_id: str | None) -> dict[str, Any]:
    name = str(record.get("name", "") or "你的投稿")
    link = f"{_site_url()}/meme/{catalog_id}" if catalog_id else _site_url()
    return send(
        {
            "email": record.get("user_email", ""),
            "nickname": record.get("user_nickname", ""),
            "title": "投稿已通过",
            "body": f"你投稿的「{name}」已经通过审核，现在可以在站内查看了：\n{link}",
        }
    )


def tag_suggestion_approved(record: dict[str, Any]) -> dict[str, Any]:
    tags = "、".join(str(tag) for tag in record.get("approved_tags", []) or record.get("tags", []))
    image_id = str(record.get("image_id", ""))
    name = str(record.get("image_name", "") or f"#{image_id}")
    link = f"{_site_url()}/meme/{image_id}" if image_id else _site_url()
    return send(
        {
            "email": record.get("user_email", ""),
            "nickname": record.get("user_nickname", ""),
            "title": "标签建议已通过",
            "body": f"你给「{name}」建议的标签已经通过审核：{tags}\n{link}",
        }
    )
