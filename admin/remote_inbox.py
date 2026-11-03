"""Pull quarantined public submissions and tag suggestions from production."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import tarfile
import uuid
from pathlib import Path
from typing import Any

from meme_pipeline.config import Config

DEFAULT_PROD_SSH = "root@47.118.50.113"
DEFAULT_PROD_API_ROOT = "/home/nailong/api"


def _host() -> str:
    return (os.getenv("MEME_PROD_SSH", DEFAULT_PROD_SSH) or DEFAULT_PROD_SSH).strip()


def _api_root() -> str:
    return (os.getenv("MEME_PROD_API_ROOT", DEFAULT_PROD_API_ROOT) or DEFAULT_PROD_API_ROOT).strip().rstrip("/")


def pull(cfg: Config) -> dict[str, Any]:
    host = _host()
    api_root = _api_root()
    remote = f"API={shlex.quote(api_root)}; mkdir -p \"$API/data/submissions\" \"$API/data/tag-suggestions\"; tar -C \"$API\" -cf - data/submissions data/tag-suggestions"
    temp_root = cfg.root / "data" / "admin" / "remote-inbox" / uuid.uuid4().hex
    temp_root.mkdir(parents=True, exist_ok=True)
    archive = temp_root / "inbox.tar"
    try:
        with archive.open("wb") as handle:
            proc = subprocess.run(
                ["ssh", "-o", "BatchMode=yes", host, "bash -lc " + shlex.quote(remote)],
                stdout=handle,
                stderr=subprocess.PIPE,
                timeout=120,
                check=False,
            )
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or b"ssh pull failed").decode("utf-8", "replace")[-2000:])
        extract = temp_root / "extracted"
        extract.mkdir()
        with tarfile.open(archive) as tar:
            for member in tar.getmembers():
                name = Path(member.name)
                if name.is_absolute() or ".." in name.parts:
                    continue
                tar.extract(member, extract)
        new_submissions = 0
        new_suggestions = 0
        remote_submissions = extract / "data" / "submissions"
        local_submissions = cfg.root / "data" / "submissions"
        local_submissions.mkdir(parents=True, exist_ok=True)
        if remote_submissions.is_dir():
            for source in sorted(remote_submissions.iterdir()):
                if not source.is_dir() or not (source / "submission.json").is_file():
                    continue
                target = local_submissions / source.name
                if target.exists():
                    continue
                shutil.copytree(source, target)
                new_submissions += 1
        remote_suggestions = extract / "data" / "tag-suggestions"
        local_suggestions = cfg.root / "data" / "tag-suggestions"
        local_suggestions.mkdir(parents=True, exist_ok=True)
        if remote_suggestions.is_dir():
            for source in sorted(remote_suggestions.glob("*.json")):
                target = local_suggestions / source.name
                if target.exists():
                    continue
                shutil.copy2(source, target)
                new_suggestions += 1
        return {
            "ok": True,
            "new_submissions": new_submissions,
            "new_tag_suggestions": new_suggestions,
            "host": host,
        }
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)
