from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from admin import ingest
from meme_pipeline.catalog import load_catalog, load_state
from meme_pipeline.config import load_config
from meme_pipeline.util import atomic_write_json


class AdminIngestTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="admin-ingest-test-")
        self.root = Path(self.temp.name)
        load_config(self.root, create=True).ensure_dirs()
        atomic_write_json(self.root / "data" / "catalog.json", [])
        (self.root / "inbox").mkdir()
        self.image = self.root / "inbox" / "new-image.png"
        Image.new("RGB", (18, 14), (255, 210, 50)).save(self.image)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_single_import_assigns_draft_id_and_assets(self) -> None:
        scan = ingest.scan_single(self.image, root=self.root)
        self.assertEqual(scan.items[0].status, "ready")
        result = ingest.commit_scan(
            scan,
            [{"token": scan.items[0].token, "name": "测试图", "tag": ["奶蛙", "开心"]}],
            root=self.root,
        )
        self.assertEqual(len(result["imported"]), 1)
        self.assertEqual(result["imported"][0]["id"], "1")
        catalog = load_catalog(load_config(self.root))
        self.assertEqual(catalog[0]["status"], "draft")
        self.assertEqual(catalog[0]["source_type"], "manual")
        self.assertIsNone(catalog[0]["source_url"])
        self.assertTrue((self.root / "img" / "raw" / "1.png").is_file())
        self.assertTrue((self.root / "img" / "converted" / "1.jpg").is_file())
        self.assertTrue((self.root / "img" / "thumbs" / "320" / "1.webp").is_file())
        self.assertTrue((self.root / "img" / "thumbs" / "640" / "1.webp").is_file())
        manifest = json.loads((self.root / "data" / "manifest.json").read_text())
        self.assertEqual(manifest["items"]["1"]["sha256"], catalog[0]["sha256"])
        thumb_manifest = json.loads((self.root / "img" / "thumbs" / "manifest.json").read_text())
        self.assertIn("1", thumb_manifest["entries"])
        state = load_state(load_config(self.root))
        self.assertEqual(state["next_id"], 2)
        self.assertEqual(json.loads((self.root / "memes.json").read_text())[0]["id"], "1")

    def test_duplicate_is_not_imported_twice(self) -> None:
        scan = ingest.scan_single(self.image, root=self.root)
        ingest.commit_scan(scan, [{"token": "0000", "name": "第一张", "tag": ["奶蛙"]}], root=self.root)
        again = ingest.scan_single(self.image, root=self.root)
        self.assertEqual(again.items[0].status, "duplicate")
        self.assertEqual(again.items[0].duplicate_id, "1")
        with self.assertRaises(Exception):
            ingest.commit_scan(again, [{"token": "0000", "name": "重复", "tag": []}], root=self.root)
        catalog = load_catalog(load_config(self.root))
        self.assertEqual(len(catalog), 1)

    def test_edit_name_tags_status_without_changing_id(self) -> None:
        scan = ingest.scan_single(self.image, root=self.root)
        ingest.commit_scan(scan, [{"token": "0000", "name": "旧名", "tag": ["奶蛙"]}], root=self.root)
        updated = ingest.edit_image("1", name="新名", tags=["奶蛙", "大笑"], status="published", root=self.root)
        self.assertEqual(updated["id"], "1")
        self.assertEqual(updated["name"], "新名")
        self.assertEqual(updated["tag"], ["奶蛙", "大笑"])
        self.assertEqual(updated["status"], "published")
        self.assertEqual(load_catalog(load_config(self.root))[0]["id"], "1")

    def test_folder_scan_reports_duplicate_and_invalid(self) -> None:
        bad = self.root / "inbox" / "bad.txt"
        bad.write_text("not an image")
        copy = self.root / "inbox" / "copy.png"
        copy.write_bytes(self.image.read_bytes())
        scan = ingest.scan_folder(self.root / "inbox", root=self.root)
        statuses = sorted((item.relative_path, item.status) for item in scan.items)
        self.assertNotIn("bad.txt", {path for path, _ in statuses})
        self.assertEqual(sum(1 for _, status in statuses if status == "duplicate"), 1)
        self.assertEqual(sum(1 for _, status in statuses if status == "ready"), 1)


if __name__ == "__main__":
    unittest.main()


class SubmissionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="admin-submission-test-")
        self.root = Path(self.temp.name)
        load_config(self.root, create=True).ensure_dirs()
        atomic_write_json(self.root / "data" / "catalog.json", [])
        self.body = self.root / "submission.png"
        Image.new("RGB", (20, 16), (245, 180, 25)).save(self.body)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_submission_is_quarantined_then_approved_with_new_id(self) -> None:
        from admin import submissions

        created = submissions.create_submission(
            self.body.read_bytes(),
            filename="submission.png",
            cfg=load_config(self.root),
            name="投稿图",
            tags=["奶蛙"],
            rights_accepted=True,
        )
        self.assertEqual(created["status"], "pending_review")
        record = submissions.list_submissions(load_config(self.root))[0]
        self.assertIsNone(record["catalog_id"])
        self.assertTrue((self.root / "data" / "submissions" / created["submission_id"] / "submission.json").is_file())
        approved = submissions.approve_submission(
            load_config(self.root),
            created["submission_id"],
            name="审核后名称",
            tags=["奶蛙", "大笑"],
            publish=True,
        )
        self.assertEqual(approved["status"], "approved")
        self.assertEqual(approved["catalog_id"], "1")
        catalog = load_catalog(load_config(self.root))
        self.assertEqual(catalog[0]["source_type"], "submission")
        self.assertEqual(catalog[0]["submission_id"], created["submission_id"])
        self.assertEqual(catalog[0]["status"], "published")

    def test_submission_rejects_without_rights(self) -> None:
        from admin import submissions

        with self.assertRaises(Exception):
            submissions.create_submission(
                self.body.read_bytes(),
                filename="submission.png",
                cfg=load_config(self.root),
                rights_accepted=False,
            )
