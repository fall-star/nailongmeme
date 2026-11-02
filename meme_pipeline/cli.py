from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import sys
from pathlib import Path
from typing import Any

from .catalog import load_catalog
from .config import load_config
from .maintenance import (
    apply_tags,
    collect_stats,
    describe_stats,
    reformat_ids,
)
from .pipeline import CrawlOptions, CrawlPipeline
from .search import SearchEngine
from .sources import get_source, list_sources
from .util import PipelineError, ValidationError, human_bytes, short, utc_stamp
from .verify import verify_library

LOGGER = logging.getLogger("meme_pipeline")


def _configure_logging(config, command: str) -> None:
    config.log_dir.mkdir(parents=True, exist_ok=True)
    log_path = config.log_dir / f"{command}-{utc_stamp()}.log"
    formatter = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(formatter)
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logging.basicConfig(level=logging.INFO, handlers=[stream, file_handler], force=True)


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m meme_pipeline",
        description="Reusable meme-library crawler: crawl, deduplicate, name and audit.",
    )
    parser.add_argument(
        "--root",
        default=".",
        help="project root containing pipeline.json, data/catalog.json and img/raw (default: cwd)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    crawl = sub.add_parser("crawl", help="crawl one source and commit new images")
    crawl.add_argument("--source", required=True, help="adapter key, e.g. nailong")
    crawl.add_argument("--dry-run", action="store_true", help="fetch metadata only; write nothing")
    crawl.add_argument("--limit", type=int, default=None, help="only consider the first N items (testing)")
    crawl.add_argument("--workers", type=int, default=None, help="override download concurrency")
    crawl.add_argument(
        "--refresh-metadata",
        action="store_true",
        help="refresh names/descriptions of existing entries from the source (ids/files unchanged)",
    )
    crawl.add_argument("--no-near-dup", action="store_true", help="skip the perceptual-hash report")
    crawl.add_argument("--keep-staging", action="store_true", help="keep downloaded staging files")
    crawl.add_argument("--no-verify", action="store_true", help="skip the post-commit audit")
    crawl.add_argument("--json", action="store_true", help="print the run report as JSON")

    verify = sub.add_parser("verify", help="audit catalog, images, manifest and state")
    verify.add_argument("--json", action="store_true", help="print a machine-readable report")
    verify.add_argument(
        "--deep",
        action="store_true",
        help="recompute perceptual hashes and compare them with the manifest",
    )
    verify.add_argument("--strict", action="store_true", help="treat warnings as failure")

    tag = sub.add_parser("tag", help="add/remove/set tags; syncs the slim delivery indexes")
    tag.add_argument("--ids", default=None, help="comma-separated ids or ranges, e.g. 1,3-8")
    tag.add_argument("--all", action="store_true", help="select every catalog entry")
    tag.add_argument("--add", action="append", default=[], help="tag to add (repeatable)")
    tag.add_argument("--remove", action="append", default=[], help="tag to remove (repeatable)")
    tag.add_argument("--set", default=None, help="replace tags with a comma-separated list")
    tag.add_argument("--dry-run", action="store_true", help="show changes without writing")

    ids = sub.add_parser("ids", help="change zero-padding of ids and filenames")
    ids.add_argument("--width", type=int, required=True, help="minimum id width (0 = no padding)")
    ids.add_argument("--dry-run", action="store_true", help="show the mapping without writing")
    ids.add_argument("--yes", action="store_true", help="required to apply the change")

    stats = sub.add_parser("stats", help="print library statistics")
    stats.add_argument("--json", action="store_true", help="print as JSON")

    listing = sub.add_parser("list", help="list catalog entries")
    listing.add_argument("--limit", type=int, default=None)
    listing.add_argument("--source", default=None)
    listing.add_argument("--tag", default=None)
    listing.add_argument("--json", action="store_true")

    search = sub.add_parser("search", help="fuzzy-search tags/names and return one image as JSON")
    search.add_argument("query", help="search word/phrase, e.g. 开心 / 开新 / 有点难过")
    search.add_argument("--min-score", type=float, default=0.80, help="fallback threshold (default: 0.80)")
    search.add_argument("--window", type=float, default=0.04, help="include near-top scores within this window (default: 0.04)")
    search.add_argument("--seed", type=int, default=None, help="seed random selection for reproducible tests")
    search.add_argument("--stable", action="store_true", help="choose the lowest id in the top group instead of random")

    sub.add_parser("sources", help="list available source adapters")
    return parser


def _cmd_crawl(config, args) -> int:
    options = CrawlOptions(
        source=args.source,
        dry_run=args.dry_run,
        limit=args.limit,
        workers=args.workers,
        refresh_metadata=args.refresh_metadata,
        near_dup=not args.no_near_dup,
        keep_staging=args.keep_staging,
        verify=not args.no_verify,
    )
    result = CrawlPipeline(config, options).execute()
    report = result.report
    if args.json:
        _print_json(report)
        return result.exit_code
    if args.dry_run:
        return result.exit_code
    print("Crawl complete")
    print(f"  source:          {report.get('source')}")
    print(f"  run:             {report.get('run_id')}")
    print(f"  fetched:         {report.get('items_fetched')}")
    print(f"  added:           {report.get('items_added')}")
    print(f"  duplicates:      {report.get('items_skipped_duplicate')}")
    print(f"  existing:        {report.get('items_skipped_existing')}")
    print(f"  repaired:        {report.get('items_repaired')}")
    print(f"  failed:          {report.get('items_failed')}")
    print(f"  catalog before:  {report.get('catalog_count_before')}")
    print(f"  catalog after:   {report.get('catalog_count_after')}")
    print(f"  next id:         {report.get('next_id')}")
    print(f"  bytes added:     {human_bytes(int(report.get('bytes_added') or 0))}")
    print(f"  report:          {report.get('report_path')}")
    if report.get("image_warnings"):
        print(f"  image warnings:  {len(report['image_warnings'])}")
    if report.get("warnings"):
        print(f"  warnings:        {len(report['warnings'])}")
    failures = report.get("failures") or []
    for failure in failures[:10]:
        print(
            f"  FAILED {failure['source_id']} ({short(str(failure['name']), 40)}): "
            f"{short(str(failure['error']), 180)}"
        )
    if len(failures) > 10:
        print(f"  ... {len(failures) - 10} more failures; see the report")
    verification = report.get("verification") or {}
    if verification:
        print(
            f"  verification:    {verification.get('checked_images', 0)} images checked, "
            f"{len(verification.get('errors', []))} error(s), "
            f"{len(verification.get('warnings', []))} warning(s)"
        )
    return result.exit_code


def _cmd_verify(config, args) -> int:
    report = verify_library(config, quiet=args.json, compute_dhash=args.deep)
    if args.json:
        _print_json(report)
    if report["errors"]:
        return 3
    if args.strict and report["warnings"]:
        return 3
    return 0


def _cmd_tag(config, args) -> int:
    result = apply_tags(
        config,
        ids_spec=args.ids,
        all_entries=args.all,
        add=args.add,
        remove=args.remove,
        set_tags=args.set,
        dry_run=args.dry_run,
    )
    prefix = "DRY RUN — " if result.dry_run else ""
    print(f"{prefix}{result.changed} of {result.selected} selected entr(ies) changed")
    for change in result.changes[:20]:
        print(
            f"  {change['id']}: {change['before']} -> {change['after']}  "
            f"({short(str(change['name']), 50)})"
        )
    if len(result.changes) > 20:
        print(f"  ... {len(result.changes) - 20} more")
    return 0


def _cmd_ids(config, args) -> int:
    result = reformat_ids(config, width=args.width, dry_run=args.dry_run, yes=args.yes)
    if result.applied:
        print(f"Reformatted {result.changed} id(s)/filename(s).")
    elif result.changed:
        print(f"{result.changed} change(s) planned.")
    else:
        print("Nothing to do; ids already use the requested width.")
    return 0


def _cmd_stats(config, args) -> int:
    stats = collect_stats(config)
    if args.json:
        _print_json(dataclasses.asdict(stats))
    else:
        print(describe_stats(stats))
    return 0


def _cmd_list(config, args) -> int:
    catalog = load_catalog(config)
    entries = catalog
    if args.source:
        entries = [entry for entry in entries if entry["source"] == args.source]
    if args.tag:
        entries = [entry for entry in entries if args.tag in entry["tag"]]
    if args.limit is not None:
        entries = entries[: args.limit]
    if args.json:
        _print_json(entries)
        return 0
    for entry in entries:
        print(f"{entry['id']}\t{entry['format']}\t{entry['source']}\t{short(entry['name'], 80)}")
    return 0


def _cmd_search(config, args) -> int:
    engine = SearchEngine.from_config(config)
    _print_json(
        engine.search(
            args.query,
            min_score=args.min_score,
            random_window=args.window,
            seed=args.seed,
            stable=args.stable,
        )
    )
    return 0


def _cmd_sources(config, args) -> int:
    for adapter_class in list_sources():
        adapter = adapter_class()
        print(f"{adapter.key}\t{adapter.display_name}\t{adapter.homepage}\tversion={adapter.version}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        config = load_config(args.root)
        if args.command != "search":
            _configure_logging(config, args.command)
        if args.command == "crawl":
            return _cmd_crawl(config, args)
        if args.command == "verify":
            return _cmd_verify(config, args)
        if args.command == "tag":
            return _cmd_tag(config, args)
        if args.command == "ids":
            return _cmd_ids(config, args)
        if args.command == "stats":
            return _cmd_stats(config, args)
        if args.command == "list":
            return _cmd_list(config, args)
        if args.command == "search":
            return _cmd_search(config, args)
        if args.command == "sources":
            return _cmd_sources(config, args)
        parser.error(f"unknown command {args.command!r}")
        return 2
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    except (ValidationError, PipelineError, KeyError, OSError, ValueError) as exc:
        if args.command != "search":
            LOGGER.error("%s", exc)
        if isinstance(exc, KeyError):
            # KeyError stringifies with quotes; show the message without them.
            message = exc.args[0] if exc.args else str(exc)
            print(f"error: {message}", file=sys.stderr)
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 1
