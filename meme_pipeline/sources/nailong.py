from __future__ import annotations

import datetime as dt
import json
from urllib.parse import urljoin

from ..config import Config
from ..models import FetchResult, SourceItem
from ..net import HttpClient
from ..util import ValidationError, clean_name, parse_iso
from .base import SourceAdapter


class NailongAdapter(SourceAdapter):
    key = "nailong"
    display_name = "奶龙Hub"
    homepage = "https://www.nailong.cc/"
    version = "1"
    api_url = "https://www.nailong.cc/api/memes"
    referer = "https://www.nailong.cc/"

    def fetch(self, config: Config) -> FetchResult:
        client = HttpClient(config, referer=self.homepage)
        raw = client.get_bytes(self.api_url, accept="application/json")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValidationError(f"nailong API returned non-JSON data: {exc}") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("memes"), list):
            raise ValidationError("nailong API response is missing the 'memes' array")

        warnings_list: list[str] = []
        items: list[SourceItem] = []
        seen_source_ids: set[str] = set()
        for index, raw_item in enumerate(payload["memes"]):
            if not isinstance(raw_item, dict):
                warnings_list.append(f"item {index}: not an object, skipped")
                continue
            source_id = raw_item.get("id")
            title = raw_item.get("title")
            image = raw_item.get("image")
            if not isinstance(source_id, str) or not source_id:
                warnings_list.append(f"item {index}: missing id, skipped")
                continue
            if source_id in seen_source_ids:
                raise ValidationError(f"nailong API contains duplicate source id {source_id!r}")
            seen_source_ids.add(source_id)
            if not isinstance(image, str) or not image.strip():
                warnings_list.append(f"item {index} ({source_id}): missing image, skipped")
                continue
            image_url = urljoin(self.homepage, image.strip())
            if not image_url.startswith(("http://", "https://")):
                warnings_list.append(f"item {index} ({source_id}): invalid image URL, skipped")
                continue
            name = clean_name(title, fallback=f"未命名表情包-{source_id[:8]}")
            if not clean_name(title):
                warnings_list.append(f"item {source_id}: empty title, used a fallback name")
            published_at = None
            created = raw_item.get("createdAt")
            if isinstance(created, str) and created.strip():
                try:
                    published_at = parse_iso(created)
                except ValueError:
                    warnings_list.append(f"item {source_id}: unparseable createdAt {created!r}")
            tags = raw_item.get("tags") if isinstance(raw_item.get("tags"), list) else []
            extra = {
                "description": raw_item.get("description") or "",
                "category": raw_item.get("category") or "",
                "kind": raw_item.get("type") or "",
                "source_label": raw_item.get("source") or "",
                "source_page_url": raw_item.get("sourceUrl") or "",
                "source_tags": tags,
                "views": raw_item.get("views"),
                "downloads": raw_item.get("downloads"),
                "featured": raw_item.get("featured"),
                "created_at": raw_item.get("createdAt"),
                "source_index": index,
            }
            items.append(
                SourceItem(
                    source_item_id=source_id,
                    name=name,
                    image_url=image_url,
                    page_url=self.homepage + "#library",
                    description=str(raw_item.get("description") or ""),
                    category=str(raw_item.get("category") or ""),
                    kind=str(raw_item.get("type") or ""),
                    source_label=str(raw_item.get("source") or ""),
                    source_page_url=str(raw_item.get("sourceUrl") or ""),
                    published_at=published_at,
                    source_order=index,
                    extra=extra,
                )
            )

        if not items:
            raise ValidationError("nailong API returned no usable memes")

        # Chronological order makes the first crawl intuitive (classic memes
        # first) and lets later additions append naturally.  The timestamp is
        # only used for initial allocation; existing ids never change.
        far_future = dt.datetime.max.replace(tzinfo=dt.timezone.utc)
        items.sort(key=lambda item: (item.published_at or far_future, item.source_item_id))
        return FetchResult(
            items=items,
            snapshot_bytes=raw,
            snapshot_suffix=".json",
            warnings=warnings_list,
        )


ADAPTER = NailongAdapter()
