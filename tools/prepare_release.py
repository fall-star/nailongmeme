#!/usr/bin/env python3
"""Build a local production release bundle without touching the server.

The bundle is intentionally explicit: metadata, API code, selected image ids
and (optionally) the built frontend.  Deploying it remains a deliberate manual
step so draft data cannot reach production by accident.
"""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def parse_ids(spec: str) -> list[str]:
    ids: list[str] = []
    for raw in spec.split(","):
        part = raw.strip()
        if not part:
            continue
        if "-" in part:
            left, right = part.split("-", 1)
            start, end = int(left), int(right)
            if start < 1 or end < start or end - start > 1000:
                raise SystemExit(f"invalid id range: {part}")
            ids.extend(str(value) for value in range(start, end + 1))
        else:
            value = int(part)
            if value < 1:
                raise SystemExit(f"invalid id: {part}")
            ids.append(str(value))
    return list(dict.fromkeys(ids))


def copy_tree(source: Path, target: Path) -> None:
    if not source.is_dir():
        raise SystemExit(f"missing directory: {source}")
    shutil.copytree(source, target, dirs_exist_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare a Nailong production release directory")
    parser.add_argument("--data-dir", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--ids", default="", help="comma/range selected image ids, e.g. 1182,1185-1187")
    parser.add_argument("--with-frontend", action="store_true", help="include frontend/dist")
    parser.add_argument("--without-api-code", action="store_true")
    args = parser.parse_args()

    root = args.data_dir.resolve()
    catalog_path = root / "data" / "catalog.json"
    if not catalog_path.is_file():
        raise SystemExit(f"catalog not found: {catalog_path}")
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    if not isinstance(catalog, list):
        raise SystemExit("catalog must be an array")

    stamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S")
    output = (args.output or (root / "release" / stamp)).resolve()
    if output.exists() and any(output.iterdir()):
        raise SystemExit(f"output directory is not empty: {output}")
    output.mkdir(parents=True, exist_ok=True)

    api_dir = output / "api"
    data_dir = api_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    selected_ids = parse_ids(args.ids) if args.ids else []
    by_id = {str(entry["id"]): entry for entry in catalog}
    missing = [image_id for image_id in selected_ids if image_id not in by_id]
    if missing:
        raise SystemExit(f"ids not in catalog: {', '.join(missing)}")

    # Main metadata is always included.  Draft entries are filtered by API 1.2.
    shutil.copy2(catalog_path, data_dir / "catalog.json")
    for name in ("memes.json",):
        source = root / name
        if source.is_file():
            shutil.copy2(source, api_dir / name)
    for name in ("state.json", "manifest.json"):
        source = root / "data" / name
        if source.is_file():
            shutil.copy2(source, data_dir / name)
    thumb_manifest = root / "img" / "thumbs" / "manifest.json"
    if thumb_manifest.is_file():
        target = api_dir / "img" / "thumbs" / "manifest.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(thumb_manifest, target)

    if not args.without_api_code:
        api_source = ROOT / "production" / "api" / "main.py"
        if api_source.is_file():
            target = api_dir / "production" / "api" / "main.py"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(api_source, target)

    copied_images: list[dict[str, Any]] = []
    for image_id in selected_ids:
        entry = by_id[image_id]
        for source, relative in [
            (root / entry["file"], Path(entry["file"])),
            (root / "img" / "converted" / f"{image_id}.jpg", Path("img/converted") / f"{image_id}.jpg"),
            (root / "img" / "converted" / f"{image_id}.gif", Path("img/converted") / f"{image_id}.gif"),
            (root / "img" / "thumbs" / "320" / f"{image_id}.webp", Path("img/thumbs/320") / f"{image_id}.webp"),
            (root / "img" / "thumbs" / "640" / f"{image_id}.webp", Path("img/thumbs/640") / f"{image_id}.webp"),
        ]:
            if not source.is_file():
                continue
            target = api_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            copied_images.append({"id": image_id, "path": relative.as_posix(), "bytes": source.stat().st_size})

    if args.with_frontend:
        copy_tree(root / "frontend" / "dist", output / "frontend")

    status_counts: dict[str, int] = {}
    for entry in catalog:
        status = str(entry.get("status", "published"))
        status_counts[status] = status_counts.get(status, 0) + 1
    report = {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": str(root),
        "catalog_entries": len(catalog),
        "status_counts": status_counts,
        "selected_ids": selected_ids,
        "images": copied_images,
        "with_frontend": args.with_frontend,
        "with_api_code": not args.without_api_code,
        "note": "Draft entries are filtered by API 1.2; deploy the API code before this catalog.",
    }
    (output / "release.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"release: {output}")
    print(f"entries: {len(catalog)}  status: {status_counts}")
    print(f"images:  {len(copied_images)}")
    print(f"frontend: {'yes' if args.with_frontend else 'no'}")
    print("next: review release.json, then follow docs/DEPLOYMENT.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
