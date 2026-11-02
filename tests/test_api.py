from __future__ import annotations

import os
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("MEME_DATA_DIR", str(ROOT))
os.environ.setdefault("MEME_PUBLIC_BASE_URL", "http://testserver")

try:
    from fastapi.testclient import TestClient
    from production.api.main import app
except ImportError:  # API optional dependencies are not installed in some dev setups.
    TestClient = None  # type: ignore[assignment]
    app = None


@unittest.skipUnless(TestClient is not None, "FastAPI test dependencies are not installed")
@unittest.skipUnless((ROOT / "img" / "thumbs" / "320").is_dir(), "local thumbnails are not generated")
class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.__exit__(None, None, None)

    def test_health_exposes_public_and_hidden_counts(self) -> None:
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["catalog_entries"], 1171)
        self.assertEqual(body["public_images"], 1164)
        self.assertEqual(body["hidden_images"], 7)
        self.assertEqual(body["review_status"], "pending_user_review")

    def test_gallery_excludes_blocked_items_and_has_thumbs(self) -> None:
        response = self.client.get("/api/gallery", params={"limit": 100})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["total"], 1164)
        ids = {item["id"] for item in body["items"]}
        self.assertNotIn("114", ids)
        item = body["items"][0]
        self.assertTrue(item["thumb_url"].endswith(f"/api/thumbs/{item['id']}/640"))
        self.assertEqual(item["thumb_320_url"], f"http://testserver/api/thumbs/{item['id']}/320")

    def test_thumbnails_and_blocked_routes(self) -> None:
        response = self.client.get("/api/thumbs/4/320")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "image/webp")
        self.assertEqual(self.client.get("/api/thumbs/114/320").status_code, 404)
        self.assertEqual(self.client.get("/api/images/114").status_code, 404)

    def test_search_list_is_multi_result(self) -> None:
        response = self.client.get("/api/search/list", params={"q": "开心", "limit": 5})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["fallback"])
        self.assertGreaterEqual(body["total"], 2)
        self.assertEqual(len(body["items"]), 5)

    def test_derived_nailong_tag_is_exposed(self) -> None:
        memes = self.client.get("/api/memes.json").json()
        derived = [item for item in memes if "奶蛙" not in item["tag"]]
        self.assertEqual(len(derived), 809)
        self.assertTrue(all("奶龙" in item["tag"] for item in derived))

    def test_gallery_compound_tag_filter(self) -> None:
        response = self.client.get("/api/gallery", params={"tags": "奶蛙,大笑", "limit": 100})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertGreater(body["total"], 0)
        self.assertTrue(all("奶蛙" in item["tag"] and "大笑" in item["tag"] for item in body["items"]))

    def test_gallery_format_filter(self) -> None:
        response = self.client.get("/api/gallery", params={"format": "gif", "limit": 5})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(all(item["format"] == "gif" for item in response.json()["items"]))

    def test_public_memes_index(self) -> None:
        response = self.client.get("/api/memes.public.json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 1164)
        self.assertEqual(len(self.client.get("/api/memes.json").json()), 1171)


if __name__ == "__main__":
    unittest.main()
