#!/usr/bin/env python3
"""Merge qwen_vision batch results into memes tag arrays.

The vision batches live in data/vision/batches and each subagent writes a
matching JSON in data/vision/results.  This script validates that every image
has exactly one 1-4 character Chinese label before touching the catalog.

Usage (run from the project root):
    python3 tools/apply_vision_tags.py --dry-run
    python3 tools/apply_vision_tags.py
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from meme_pipeline.catalog import (
    load_catalog,
    load_state,
    save_catalog,
    save_state,
)
from meme_pipeline.config import load_config
from meme_pipeline.util import iso_local, new_run_id
from meme_pipeline.verify import verify_library

ALLOWED = {"嘉豪", "伤感", "开心", "大笑", "忧郁"}
LABEL_RE = re.compile(r"^[\u4e00-\u9fff]{1,4}$")
BATCH_RE = re.compile(r"^batch-(\d{3})\.json$")


def _load_expected(batch_dir: Path) -> list[dict[str, Any]]:
    index_path = batch_dir / "index.json"
    if not index_path.exists():
        raise SystemExit(f"missing batch index: {index_path}")
    index = json.loads(index_path.read_text(encoding="utf-8"))
    expected: list[dict[str, Any]] = []
    for item in index["batches"]:
        payload = json.loads((batch_dir / f"{item['batch_id']}.json").read_text(encoding="utf-8"))
        for image in payload["images"]:
            expected.append({"id": image["id"], "path": image["path"], "batch": item["batch_id"]})
    return expected


def _load_results(results_dir: Path) -> tuple[dict[str, dict[str, Any]], list[str]]:
    merged: dict[str, dict[str, Any]] = {}
    problems: list[str] = []
    for path in sorted(results_dir.glob("batch-*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            problems.append(f"{path.name}: cannot read JSON: {exc}")
            continue
        batch_id = payload.get("batch_id") or path.stem
        results = payload.get("results")
        if not isinstance(results, list):
            problems.append(f"{path.name}: results is not an array")
            continue
        for result in results:
            if not isinstance(result, dict):
                problems.append(f"{path.name}: result is not an object")
                continue
            image_id = result.get("id")
            if not isinstance(image_id, str) or not image_id:
                problems.append(f"{path.name}: result without id")
                continue
            if image_id in merged:
                problems.append(f"{path.name}: duplicate id {image_id}")
            merged[image_id] = {
                "id": image_id,
                "label": str(result.get("label", "")).strip(),
                "raw": str(result.get("raw", "")).strip(),
                "error": str(result.get("error", "")).strip(),
                "batch": batch_id,
            }
    return merged, problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate and apply qwen vision tags")
    parser.add_argument("--batch-dir", default="data/vision/batches")
    parser.add_argument("--results-dir", default="data/vision/results")
    parser.add_argument("--dry-run", action="store_true", help="validate/report without writing catalog")
    args = parser.parse_args(argv)

    config = load_config(".")
    catalog = load_catalog(config)
    expected = _load_expected(Path(args.batch_dir))
    expected_ids = [item["id"] for item in expected]
    results, problems = _load_results(Path(args.results_dir))
    labels: dict[str, str] = {}
    invalid = []
    for image_id in expected_ids:
        result = results.get(image_id)
        if result is None:
            invalid.append(f"missing result for id {image_id}")
            continue
        label = result["label"]
        if not LABEL_RE.fullmatch(label) or label in {"其他", "无", "未知"}:
            invalid.append(f"id {image_id}: invalid label {label!r} (raw={result['raw']!r})")
            continue
        labels[image_id] = label
    extra = sorted(set(results) - set(expected_ids), key=lambda value: (len(value), value))
    if extra:
        problems.append(f"extra result ids: {extra[:20]}")
    if problems:
        print("\n".join(problems), file=sys.stderr)
    if invalid:
        print("\n".join(invalid[:50]), file=sys.stderr)
        if len(invalid) > 50:
            print(f"... {len(invalid) - 50} more invalid/missing", file=sys.stderr)
        print(f"not applying: {len(invalid)} invalid/missing of {len(expected_ids)}", file=sys.stderr)
        return 1
    if len(labels) != len(expected_ids):
        print(f"not applying: expected {len(expected_ids)}, got {len(labels)}", file=sys.stderr)
        return 1

    distribution = Counter(labels.values())
    allowed_distribution = {key: value for key, value in distribution.items() if key in ALLOWED}
    generated_distribution = {key: value for key, value in distribution.items() if key not in ALLOWED}
    by_id = {entry["id"]: entry for entry in catalog}
    rows = []
    for item in expected:
        entry = by_id.get(item["id"])
        if entry is None:
            print(f"not applying: catalog missing id {item['id']}", file=sys.stderr)
            return 1
        label = labels[item["id"]]
        old_tags = list(entry["tag"])
        new_tags = old_tags if label in old_tags else old_tags + [label]
        rows.append(
            {
                "id": item["id"],
                "label": label,
                "raw": results[item["id"]]["raw"],
                "batch": item["batch"],
                "old_tags": old_tags,
                "new_tags": new_tags,
            }
        )

    print(f"expected: {len(expected_ids)}")
    print(f"valid labels: {len(labels)}")
    print(f"allowed labels: {dict(sorted(allowed_distribution.items()))}")
    print(f"generated labels: {dict(sorted(generated_distribution.items()))}")
    if args.dry_run:
        print("DRY RUN — catalog not modified")
        return 0

    for row in rows:
        by_id[row["id"]]["tag"] = row["new_tags"]
    run_id = new_run_id()
    catalog_sha = save_catalog(config, catalog, run_id)
    state = load_state(config)
    state.update(
        {
            "catalog_sha256": catalog_sha,
            "count": len(catalog),
            "next_id": max(
                int(state.get("next_id") or 1),
                max(int(entry["id"]) for entry in catalog) + 1,
            ),
            "last_run_id": run_id,
            "updated_at": iso_local(),
        }
    )
    save_state(config, state)
    verification = verify_library(config, quiet=True)

    labels_path = Path("data/vision/labels.json")
    labels_path.write_text(
        json.dumps(
            {
                "generated_at": iso_local(),
                "run_id": run_id,
                "count": len(rows),
                "allowed": allowed_distribution,
                "generated": generated_distribution,
                "items": rows,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    report_path = Path("reports/vision-tags-applied.json")
    report_path.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "applied_at": iso_local(),
                "count": len(rows),
                "allowed": allowed_distribution,
                "generated": generated_distribution,
                "catalog_sha256": catalog_sha,
                "verification": verification,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    with Path("reports/vision-tags.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["id", "label", "raw", "batch", "old_tags", "new_tags"])
        for row in rows:
            writer.writerow(
                [
                    row["id"],
                    row["label"],
                    row["raw"],
                    row["batch"],
                    json.dumps(row["old_tags"], ensure_ascii=False),
                    json.dumps(row["new_tags"], ensure_ascii=False),
                ]
            )
    print(f"applied {len(rows)} tags, verify_ok={verification['ok']}, errors={len(verification['errors'])}")
    return 0 if verification["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
