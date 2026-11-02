"""Template for adding another meme site.

Copy this file to ``meme_pipeline/sources/<site>.py``, rename the class/key,
implement ``fetch`` and uncomment ``ADAPTER = ...``.  The loader discovers
modules in this package automatically; the shared pipeline then handles
download validation, deduplication, id allocation, the catalog and the audit.

Key rules (see CRAWL_SPEC.md):
  * return every public meme, not just the first page;
  * use a stable ``source_item_id`` (site id, slug, or stable URL hash);
  * never download/normalise/convert images here — return the original URL;
  * do not copy source tags into ``tag``; ``tag`` starts empty;
  * prefer the site's JSON API; use browser automation only when unavoidable;
  * respect robots.txt, terms of service and a polite request rate.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any
from urllib.parse import urljoin

from ..config import Config
from ..models import FetchResult, SourceItem
from ..net import HttpClient
from ..util import ValidationError, clean_name, parse_iso
from .base import SourceAdapter


class MySiteAdapter(SourceAdapter):
    key = "mysite"
    display_name = "My Site"
    homepage = "https://example.com/"
    version = "1"
    listing_url = "https://example.com/api/memes"
    referer = "https://example.com/"

    def fetch(self, config: Config) -> FetchResult:
        # 1) Get the complete listing.  Prefer the API the front-end calls.
        client = HttpClient(config, referer=self.referer)
        raw = client.get_bytes(self.listing_url, accept="application/json")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValidationError(f"listing returned non-JSON: {exc}") from exc

        records = payload.get("items")
        if not isinstance(records, list):
            raise ValidationError("listing JSON has no items array")

        warnings: list[str] = []
        items: list[SourceItem] = []
        for index, record in enumerate(records):
            if not isinstance(record, dict):
                warnings.append(f"item {index}: not an object, skipped")
                continue
            image = record.get("image") or record.get("image_url")
            if not isinstance(image, str) or not image.strip():
                warnings.append(f"item {index}: no image URL, skipped")
                continue
            image_url = urljoin(self.homepage, image.strip())
            if not image_url.startswith(("http://", "https://")):
                warnings.append(f"item {index}: invalid image URL, skipped")
                continue

            # Stable identity: prefer the site's id; otherwise hash the URL.
            source_id = str(record.get("id") or "").strip()
            if not source_id:
                source_id = "url-" + hashlib.sha256(image_url.encode("utf-8")).hexdigest()[:16]

            title = clean_name(record.get("title") or record.get("name"))
            name = title or f"未命名表情包-{source_id[:8]}"
            if not title:
                warnings.append(f"item {source_id}: empty title, used a fallback name")

            published_at = None
            raw_date = record.get("created_at") or record.get("published_at")
            if isinstance(raw_date, str) and raw_date.strip():
                try:
                    published_at = parse_iso(raw_date)
                except ValueError:
                    warnings.append(f"item {source_id}: unparseable date {raw_date!r}")

            items.append(
                SourceItem(
                    source_item_id=source_id,
                    name=name,
                    image_url=image_url,
                    page_url=self.homepage,
                    description=str(record.get("description") or ""),
                    category=str(record.get("category") or ""),
                    kind=str(record.get("type") or ""),
                    source_label=str(record.get("source") or ""),
                    source_page_url=str(record.get("source_url") or ""),
                    published_at=published_at,
                    source_order=index,
                    extra={"source_tags": record.get("tags") or []},
                )
            )

        if not items:
            raise ValidationError("listing returned no usable items")
        # Initial ids are assigned chronologically when the source has dates.
        items.sort(key=lambda item: (item.published_at is None, item.published_at, item.source_item_id))
        return FetchResult(items=items, snapshot_bytes=raw, snapshot_suffix=".json", warnings=warnings)


# The loader only registers a module if it exposes ADAPTER.
# ADAPTER = MySiteAdapter()


# ---------------------------------------------------------------------------
# Static HTML without JavaScript
# ---------------------------------------------------------------------------
# Use urllib + html.parser (or lxml, which is available in the DSH runtime) to
# parse the card list.  Look for <img alt="..."> and the nearest title.  If the
# site really paginates on the server, follow ?page=N until a page is empty.
# The pipeline still de-duplicates everything by bytes, so repeated cards are
# harmless.
#
# ---------------------------------------------------------------------------
# JavaScript / "加载更多" sites
# ---------------------------------------------------------------------------
# Install once:  pip install playwright && playwright install chromium
# Then:
#
#   from playwright.sync_api import sync_playwright
#
#   with sync_playwright() as p:
#       browser = p.chromium.launch(headless=True)
#       page = browser.new_page(user_agent=config.user_agent)
#       page.goto(self.homepage, wait_until="networkidle")
#       # Click the site's own "load more" control until it disappears/stops.
#       for _ in range(200):
#           button = page.locator("text=加载更多")
#           if button.count() == 0:
#               break
#           before = page.locator("img").count()
#           button.first.click()
#           page.wait_for_timeout(800)
#           if page.locator("img").count() == before:
#               break
#       cards = page.locator("main img").all()
#       ...  # read alt + src, resolve relative URLs, return SourceItem list
#       browser.close()
#
# Keep the browser adapter separate from this template if it grows large.
