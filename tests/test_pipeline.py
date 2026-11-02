from __future__ import annotations

import json
import shutil
import tempfile
import threading
import unittest
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path

from meme_pipeline.catalog import load_catalog, load_manifest, load_state
from meme_pipeline.config import load_config
from meme_pipeline.maintenance import apply_tags, reformat_ids
from meme_pipeline.models import FetchResult, SourceItem
from meme_pipeline.pipeline import CrawlOptions, CrawlPipeline
from meme_pipeline.sources import register_source
from meme_pipeline.sources.naihub import EXCLUDED_CATEGORY_IDS, NaihubAdapter
from meme_pipeline.sources.base import SourceAdapter
from meme_pipeline.util import ValidationError, parse_iso
from meme_pipeline.verify import verify_library

try:
    from PIL import Image

    HAS_PILLOW = True
except ImportError:  # pragma: no cover
    HAS_PILLOW = False


def _encode(fmt: str, color=(255, 0, 0), size=(12, 12)) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", size, color).save(buffer, format=fmt)
    return buffer.getvalue()


def _encode_gif() -> bytes:
    buffer = BytesIO()
    frames = [Image.new("RGB", (10, 10), (i * 40, 0, 0)) for i in range(3)]
    frames[0].save(
        buffer,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=80,
        loop=0,
    )
    return buffer.getvalue()


class _Handler(BaseHTTPRequestHandler):
    images: dict[str, tuple[bytes, str]] = {}
    items: list[dict] = []

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/api/items":
            payload = json.dumps({"items": self.items}, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        entry = self.images.get(self.path)
        if entry is None:
            self.send_error(404)
            return
        body, content_type = entry
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args) -> None:
        pass


class PipelineEndToEndTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not HAS_PILLOW:
            raise unittest.SkipTest("Pillow is required for the image fixture tests")
        cls.png = _encode("PNG", (255, 0, 0))
        cls.webp = _encode("WEBP", (0, 0, 255))
        cls.gif = _encode_gif()
        cls.jpeg = _encode("JPEG", (0, 255, 0))
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.base_url = f"http://127.0.0.1:{cls.server.server_address[1]}"
        _Handler.images = {
            "/img/a.png": (cls.png, "image/png"),
            "/img/b.jpg": (cls.gif, "image/jpeg"),  # deliberately mislabelled
            "/img/c.webp": (cls.webp, "image/webp"),
            "/img/c-copy.webp": (cls.webp, "image/webp"),
            "/img/d.jpeg": (cls.jpeg, "image/jpeg"),
        }
        _Handler.items = []
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="meme-pipeline-test-")
        self.root = Path(self.tempdir.name)
        (self.root / "img" / "raw").mkdir(parents=True)
        config = {
            "paths": {
                "catalog": "memes.json",
                "image_dir": "img/raw",
                "manifest": "data/manifest.json",
                "state": "data/state.json",
                "sources": "data/sources.json",
                "snapshot_dir": "data/snapshots",
                "report_dir": "reports",
                "backup_dir": "data/backups",
                "staging_dir": "data/staging",
                "journal_dir": "data/journal",
                "log_dir": "data/logs",
            },
            "ids": {"min_width": 0, "max_digits": 5},
            "download": {"workers": 2, "timeout_seconds": 10, "retries": 1, "max_bytes": 5_000_000},
            "dedupe": {"near_duplicate_hamming": 4, "near_duplicate_max_pairs": 50},
        }
        (self.root / "pipeline.json").write_text(json.dumps(config), encoding="utf-8")
        self.config = load_config(self.root)
        self.key = f"test_{uuid.uuid4().hex[:10]}"
        self.items = self._items()
        base_url = self.base_url

        class TestAdapter(SourceAdapter):
            key = self.key
            display_name = "Test source"
            homepage = base_url
            version = "1"

            def fetch(self, config) -> FetchResult:
                current_items = list(_Handler.items)
                source_items = [
                    SourceItem(
                        source_item_id=item["id"],
                        name=item["title"],
                        image_url=f"{base_url}{item['image']}",
                        page_url=base_url + "/",
                        description="test",
                        category="test",
                        kind="static",
                        source_label="fixture",
                        published_at=parse_iso(item["published_at"]) if item.get("published_at") else None,
                        source_order=index,
                    )
                    for index, item in enumerate(current_items)
                ]
                return FetchResult(
                    items=source_items,
                    snapshot_bytes=json.dumps({"items": current_items}).encode("utf-8"),
                )

        register_source(TestAdapter)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _items(self) -> list[dict]:
        return [
            {
                "id": "src-a",
                "title": "A 图",
                "image": "/img/a.png",
                "published_at": "2026-01-01T00:00:00+00:00",
            },
            {
                "id": "src-b",
                "title": "B 图",
                "image": "/img/b.jpg",
                "published_at": "2026-01-02T00:00:00+00:00",
            },
            {
                "id": "src-c",
                "title": "C 图",
                "image": "/img/c.webp",
                "published_at": "2026-01-03T00:00:00+00:00",
            },
            {
                "id": "src-c-copy",
                "title": "C 图重复",
                "image": "/img/c-copy.webp",
                "published_at": "2026-01-04T00:00:00+00:00",
            },
        ]

    def _crawl(self, *, verify: bool = True) -> object:
        self.items = list(_Handler.items)
        options = CrawlOptions(source=self.key, near_dup=False, verify=verify)
        return CrawlPipeline(self.config, options).execute()

    def test_crawl_dedupe_idempotent_and_repair(self) -> None:
        self.items = self._items()
        _Handler.items = self.items
        result = self._crawl()
        self.assertEqual(result.exit_code, 0, result.report)
        self.assertEqual(result.added, 3)
        catalog = load_catalog(self.config)
        self.assertEqual([entry["id"] for entry in catalog], ["1", "2", "3"])
        self.assertEqual([entry["format"] for entry in catalog], ["png", "gif", "webp"])
        self.assertEqual(catalog[1]["file"], "img/raw/2.gif")
        self.assertEqual(catalog[1]["name"], "B 图")
        self.assertTrue((self.root / "img/raw/2.gif").exists())
        manifest = load_manifest(self.config)
        self.assertEqual(len(manifest["items"]), 3)
        self.assertEqual(len(manifest["items"]["3"]["aliases"]), 1)
        self.assertEqual(manifest["items"]["3"]["aliases"][0]["source_id"], "src-c-copy")
        state = load_state(self.config)
        self.assertEqual(state["count"], 3)
        self.assertEqual(state["next_id"], 4)
        self.assertTrue(verify_library(self.config, quiet=True)["ok"])

        # A second crawl must not add or renumber anything.  The duplicate was
        # recorded as an alias, so it is now recognised as an existing item.
        result = self._crawl()
        self.assertEqual(result.exit_code, 0, result.report)
        self.assertEqual(result.added, 0)
        self.assertEqual(result.report["items_skipped_existing"], 4)
        self.assertEqual(result.report["items_skipped_duplicate"], 0)
        self.assertEqual(len(load_catalog(self.config)), 3)

        # New items append after the highest id.
        _Handler.items = self._items() + [
            {
                "id": "src-d",
                "title": "D 图",
                "image": "/img/d.jpeg",
                "published_at": "2026-01-05T00:00:00+00:00",
            }
        ]
        self.items = list(_Handler.items)
        result = self._crawl()
        self.assertEqual(result.exit_code, 0, result.report)
        self.assertEqual(result.added, 1)
        catalog = load_catalog(self.config)
        self.assertEqual([entry["id"] for entry in catalog], ["1", "2", "3", "4"])
        self.assertEqual(catalog[-1]["format"], "jpeg")

    def test_missing_file_is_restored_without_renumbering(self) -> None:
        self.items = self._items()
        _Handler.items = self.items
        self._crawl()
        target = self.root / "img/raw/1.png"
        target.unlink()
        result = self._crawl()
        self.assertEqual(result.exit_code, 0, result.report)
        self.assertEqual(result.report["items_repaired"], 1)
        self.assertTrue(target.exists())
        self.assertEqual([entry["id"] for entry in load_catalog(self.config)], ["1", "2", "3"])

    def test_verify_detects_corruption_and_orphans(self) -> None:
        self.items = self._items()
        _Handler.items = self.items
        self._crawl()
        (self.root / "img/raw/1.png").write_bytes(b"not an image")
        (self.root / "img/raw/999.png").write_bytes(b"orphan")
        report = verify_library(self.config, quiet=True)
        self.assertFalse(report["ok"])
        joined = "\n".join(report["errors"])
        self.assertIn("sha256 mismatch", joined)
        self.assertIn("orphan", joined)

    def test_ids_skip_orphan_files(self) -> None:
        self.items = self._items()[:2]
        _Handler.items = self.items
        self._crawl()
        state = load_state(self.config)
        state["next_id"] = 10
        (self.root / "data/state.json").write_text(json.dumps(state), encoding="utf-8")
        (self.root / "img/raw/10.webp").write_bytes(self.webp)
        _Handler.items = self._items()[:3] + [
            {
                "id": "src-d",
                "title": "D 图",
                "image": "/img/d.jpeg",
                "published_at": "2026-01-05T00:00:00+00:00",
            }
        ]
        self.items = list(_Handler.items)
        result = self._crawl(verify=False)
        self.assertEqual(result.exit_code, 0, result.report)
        catalog = load_catalog(self.config)
        ids = [entry["id"] for entry in catalog]
        self.assertNotIn("10", ids)
        self.assertEqual(ids[-2:], ["11", "12"])

    def test_tags_and_id_repadding(self) -> None:
        self.items = self._items()
        _Handler.items = self.items
        self._crawl()
        tagged = apply_tags(
            self.config,
            ids_spec="1,2",
            all_entries=False,
            add=["可爱", "表情"],
        )
        self.assertEqual(tagged.changed, 2)
        catalog = load_catalog(self.config)
        self.assertEqual(catalog[0]["tag"], ["可爱", "表情"])
        self.assertTrue(verify_library(self.config, quiet=True)["ok"])

        repadded = reformat_ids(self.config, width=5, yes=True)
        self.assertTrue(repadded.applied)
        catalog = load_catalog(self.config)
        self.assertEqual([entry["id"] for entry in catalog], ["00001", "00002", "00003"])
        self.assertTrue((self.root / "img/raw/00001.png").exists())
        self.assertFalse((self.root / "img/raw/1.png").exists())
        self.assertTrue(verify_library(self.config, quiet=True)["ok"])
        saved_config = json.loads((self.root / "pipeline.json").read_text(encoding="utf-8"))
        self.assertEqual(saved_config["ids"]["min_width"], 5)

        # Reload the config so future allocations use the new width.
        self.config = load_config(self.root)
        _Handler.items = self._items()[:3] + [
            {
                "id": "src-d",
                "title": "D 图",
                "image": "/img/d.jpeg",
                "published_at": "2026-01-05T00:00:00+00:00",
            }
        ]
        self.items = list(_Handler.items)
        result = self._crawl()
        self.assertEqual(result.exit_code, 0, result.report)
        self.assertEqual(result.added, 1)
        catalog = load_catalog(self.config)
        self.assertEqual(catalog[-1]["id"], "00004")
        self.assertEqual(catalog[-1]["file"], "img/raw/00004.jpg")
        self.assertTrue(verify_library(self.config, quiet=True)["ok"])

    def test_refuses_to_crawl_when_catalog_is_missing_but_images_exist(self) -> None:
        self.items = self._items()[:1]
        _Handler.items = self.items
        self._crawl()
        (self.root / "memes.json").unlink()
        with self.assertRaises(ValidationError):
            self._crawl()

    def test_duplicate_catalog_rejected(self) -> None:
        self.items = self._items()[:2]
        _Handler.items = self.items
        self._crawl()
        catalog = load_catalog(self.config)
        broken = catalog + [dict(catalog[0])]
        (self.root / "memes.json").write_text(
            json.dumps(broken, ensure_ascii=False), encoding="utf-8"
        )
        report = verify_library(self.config, quiet=True)
        self.assertFalse(report["ok"])
        self.assertTrue(any("duplicate" in error for error in report["errors"]))


if __name__ == "__main__":
    unittest.main()


class NaihubAdapterUnitTest(unittest.TestCase):
    def test_relative_source_resolves_to_immutable_cdn_and_github_urls(self) -> None:
        cdn, raw, blob, source_id, owner, commit, path = NaihubAdapter._resolve(
            "Musemantic/8B5BA9FC738254DD52FFA08106C87C44AD54DF1C/assets/memes/naiwa_0001.gif"
        )
        self.assertEqual(owner, "musemantic")
        self.assertEqual(commit, "8b5ba9fc738254dd52ffa08106c87c44ad54df1c")
        self.assertEqual(path, "assets/memes/naiwa_0001.gif")
        self.assertEqual(source_id, "musemantic/8b5ba9fc738254dd52ffa08106c87c44ad54df1c/assets/memes/naiwa_0001.gif")
        self.assertEqual(
            cdn,
            "https://cdn.jsdelivr.net/gh/musemantic/NaiLoong@"
            "8b5ba9fc738254dd52ffa08106c87c44ad54df1c/assets/memes/naiwa_0001.gif",
        )
        self.assertIn("raw.githubusercontent.com/musemantic/NaiLoong/", raw)
        self.assertIn("github.com/musemantic/NaiLoong/blob/", blob)

    def test_school_emblem_is_explicitly_excluded(self) -> None:
        self.assertIn("nai-emblem", EXCLUDED_CATEGORY_IDS)
