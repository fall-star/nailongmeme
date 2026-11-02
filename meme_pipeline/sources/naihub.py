from __future__ import annotations

import json
import re
from datetime import datetime
from urllib.parse import quote, urljoin

from ..config import Config
from ..models import FetchResult, SourceItem
from ..net import HttpClient
from ..util import ValidationError, clean_name, iso_local
from .base import SourceAdapter

# The user explicitly asked for every category except the school-emblem
# category.  Keep both the id and the visible name as a safety net.
EXCLUDED_CATEGORY_IDS = {"nai-emblem"}
EXCLUDED_CATEGORY_NAMES = {"奶校徽"}

_RELATIVE_RE = re.compile(r"^([A-Za-z0-9-]+)/([0-9a-fA-F]{40})/(.+)$")


class NaihubAdapter(SourceAdapter):
    key = "naihub"
    display_name = "奶-hub"
    homepage = "https://nai-hub.com/"
    version = "1"
    manifest_url = "https://nai-hub.com/data/manifest.json"
    referer = "https://nai-hub.com/"

    def fetch(self, config: Config) -> FetchResult:
        client = HttpClient(config, referer=self.homepage)
        warnings: list[str] = []

        manifest_raw = client.get_bytes(self.manifest_url, accept="application/json")
        try:
            manifest = json.loads(manifest_raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValidationError(f"nai-hub manifest is not valid JSON: {exc}") from exc
        if not isinstance(manifest, list) or not manifest:
            raise ValidationError("nai-hub manifest is not a non-empty array")

        snapshot_categories: dict[str, dict[str, list]] = {}
        excluded_counts: dict[str, int] = {}
        items: list[SourceItem] = []
        seen_source_ids: set[str] = set()

        for category in manifest:
            if not isinstance(category, dict):
                warnings.append("manifest contains a non-object category; skipped")
                continue
            category_id = str(category.get("id") or "")
            category_name = clean_name(category.get("name"))
            subcategories = category.get("subcategories")
            if not category_id or not isinstance(subcategories, list):
                warnings.append(f"category {category_name or category_id!r}: malformed, skipped")
                continue
            excluded = (
                category_id in EXCLUDED_CATEGORY_IDS
                or category_name in EXCLUDED_CATEGORY_NAMES
            )
            snapshot_categories.setdefault(category_id, {})
            for subcategory in subcategories:
                if not isinstance(subcategory, dict):
                    warnings.append(f"category {category_name}: malformed subcategory; skipped")
                    continue
                sub_id = str(subcategory.get("id") or "")
                sub_name = clean_name(subcategory.get("name"))
                sub_file = subcategory.get("file")
                if not sub_id or not isinstance(sub_file, str) or not sub_file.strip():
                    warnings.append(f"category {category_name}: subcategory without file; skipped")
                    continue
                data_url = urljoin(self.homepage, "data/" + sub_file.strip())
                if not data_url.startswith(self.homepage):
                    raise ValidationError(f"refusing category data outside the site: {data_url}")
                raw = client.get_bytes(data_url, accept="application/json")
                try:
                    data = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ValidationError(f"{data_url} is not valid JSON: {exc}") from exc
                if not isinstance(data, list):
                    raise ValidationError(f"{data_url} does not contain an array")
                snapshot_categories[category_id][sub_id] = data

                if excluded:
                    excluded_counts[f"{category_name}/{sub_name}"] = len(data)
                    continue

                for index, raw_item in enumerate(data):
                    if not isinstance(raw_item, dict):
                        warnings.append(f"{category_name}/{sub_name} item {index}: not an object; skipped")
                        continue
                    title = raw_item.get("title")
                    relative_url = raw_item.get("url")
                    if not isinstance(title, str) or not title.strip():
                        warnings.append(
                            f"{category_name}/{sub_name} item {index}: missing title; skipped"
                        )
                        continue
                    if not isinstance(relative_url, str) or not relative_url.strip():
                        warnings.append(
                            f"{category_name}/{sub_name} item {index}: missing url; skipped"
                        )
                        continue
                    relative_url = relative_url.strip()
                    image_url, raw_url, blob_url, source_id, owner, commit, path = self._resolve(
                        relative_url
                    )
                    if source_id in seen_source_ids:
                        raise ValidationError(f"nai-hub contains duplicate source id {source_id!r}")
                    seen_source_ids.add(source_id)

                    name = clean_name(title, fallback=f"未命名表情包-{source_id[-8:]}")
                    if not clean_name(title):
                        warnings.append(f"item {source_id}: empty title; used a fallback name")
                    tags = raw_item.get("tags")
                    if tags is None:
                        tags = []
                        warnings.append(f"item {source_id}: null tags treated as an empty list")
                    if not isinstance(tags, list):
                        warnings.append(f"item {source_id}: tags is not an array; ignored")
                        tags = []
                    items.append(
                        SourceItem(
                            source_item_id=source_id,
                            name=name,
                            image_url=image_url,
                            page_url=self.homepage + "#gallery",
                            description=f"{category_name} · {sub_name}",
                            category=category_name,
                            kind=sub_name,
                            source_label="nai-hub",
                            source_page_url=blob_url,
                            published_at=None,
                            source_order=len(items),
                            extra={
                                "category_id": category_id,
                                "category_name": category_name,
                                "subcategory_id": sub_id,
                                "subcategory_name": sub_name,
                                "source_tags": tags,
                                "relative_url": relative_url,
                                "owner": owner,
                                "commit": commit,
                                "path": path,
                                "repo": f"{owner}/NaiLoong" if owner else "",
                                "raw_url": raw_url,
                                "cdn_url": image_url,
                                "data_file": sub_file,
                            },
                        )
                    )

        if not items:
            raise ValidationError("nai-hub returned no usable items after excluding 奶校徽")
        if excluded_counts:
            details = ", ".join(f"{key}={value}" for key, value in sorted(excluded_counts.items()))
            warnings.append(f"excluded 奶校徽 (nai-emblem) by request: {details}")

        snapshot = {
            "site": self.homepage,
            "manifest_url": self.manifest_url,
            "fetched_at": iso_local(),
            "excluded_category_ids": sorted(EXCLUDED_CATEGORY_IDS),
            "excluded_category_names": sorted(EXCLUDED_CATEGORY_NAMES),
            "excluded_counts": excluded_counts,
            "manifest": manifest,
            "categories": snapshot_categories,
        }
        snapshot_bytes = json.dumps(snapshot, ensure_ascii=False, indent=2).encode("utf-8")
        return FetchResult(
            items=items,
            snapshot_bytes=snapshot_bytes,
            snapshot_suffix=".json",
            warnings=warnings,
        )

    @staticmethod
    def _resolve(relative_url: str) -> tuple[str, str, str, str, str, str, str]:
        """Return (cdn_url, raw_url, github_blob_url, source_id, owner, commit, path)."""
        match = _RELATIVE_RE.match(relative_url)
        if match:
            owner = match.group(1).lower()
            commit = match.group(2).lower()
            path = match.group(3)
            quoted_path = "/".join(quote(part, safe="") for part in path.split("/"))
            cdn_url = (
                f"https://cdn.jsdelivr.net/gh/{owner}/NaiLoong@{commit}/{quoted_path}"
            )
            raw_url = (
                f"https://raw.githubusercontent.com/{owner}/NaiLoong/{commit}/{quoted_path}"
            )
            blob_url = (
                f"https://github.com/{owner}/NaiLoong/blob/{commit}/{quoted_path}"
            )
            source_id = f"{owner}/{commit}/{path}"
            return cdn_url, raw_url, blob_url, source_id, owner, commit, path

        # Be tolerant of a future absolute URL, but do not pretend it is an
        # immutable commit-addressed asset.
        if relative_url.startswith(("http://", "https://")):
            return relative_url, relative_url, relative_url, relative_url, "", "", ""
        raise ValidationError(f"unsupported nai-hub image reference: {relative_url!r}")


ADAPTER = NaihubAdapter()
