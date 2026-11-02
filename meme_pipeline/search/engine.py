from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..catalog import load_catalog
from ..config import Config
from .aliases import AliasTable, Concept
from .normalize import (
    char_ngrams,
    dice,
    is_subsequence,
    name_core,
    normalize,
    sequence_ratio,
)
from .pinyin import PinyinMap

METHOD_PRIORITY = {
    "exact_tag": 100,
    "name_exact": 95,
    "alias_tag": 90,
    "tag_contains": 80,
    "name_contains": 70,
    "pinyin_tag": 60,
    "pinyin_name": 55,
    "fuzzy_tag": 40,
    "fuzzy_name": 30,
    "fallback": 0,
}


@dataclass
class Match:
    index: int
    score: float
    field: str
    value: str
    method: str
    canonical: str | None = None

    @property
    def priority(self) -> int:
        return METHOD_PRIORITY.get(self.method, 0)


class SearchError(RuntimeError):
    pass


class SearchEngine:
    """Small in-memory fuzzy search over tags and names.

    It intentionally uses only the standard library plus the static pinyin map:
    no embedding model, vector store or network call.
    """

    def __init__(
        self,
        root: Path,
        entries: list[dict[str, Any]],
        *,
        aliases_path: Path | None = None,
        pinyin_path: Path | None = None,
        require_images: bool = True,
    ) -> None:
        self.root = Path(root)
        self.require_images = require_images
        self.aliases = AliasTable(
            aliases_path if aliases_path is not None else self.root / "data/search/aliases.json"
        )
        self.pinyin = PinyinMap(
            pinyin_path if pinyin_path is not None else self.root / "data/search/pinyin.json"
        )
        self.alias_term_pinyin: list[tuple[str, Concept, tuple[str, ...]]] = []
        for concept in self.aliases.concepts.values():
            for term in concept.terms:
                if len(term) < 2:
                    continue
                syllables = self.pinyin.syllables(term)
                if syllables:
                    self.alias_term_pinyin.append((term, concept, syllables))

        self.entries: list[dict[str, Any]] = []
        self.images: list[str] = []
        self.name_norm: list[str] = []
        self.core_norm: list[str] = []
        self.name_syl: list[tuple[str, ...] | None] = []
        self.core_syl: list[tuple[str, ...] | None] = []
        self.entry_tags: list[list[str]] = []
        self.tag_index: dict[str, list[int]] = {}
        self.tag_display: dict[str, str] = {}
        self.tag_syl: dict[str, tuple[str, ...] | None] = {}

        for entry in entries:
            if not isinstance(entry, dict) or entry.get("pending") is True:
                continue
            entry_id = str(entry.get("id", ""))
            name = str(entry.get("name", ""))
            tags = [str(tag) for tag in entry.get("tag", []) if str(tag).strip()]
            # Library rule: anything without 奶蛙 is considered 奶龙.
            if "奶蛙" not in tags and "奶龙" not in tags:
                tags.append("奶龙")
            entry["tag"] = tags
            image = self._resolve_image(entry_id)
            if image is None:
                continue
            index = len(self.entries)
            self.entries.append(entry)
            self.images.append(image)
            self.name_norm.append(normalize(name))
            core = name_core(name)
            self.core_norm.append(core)
            self.entry_tags.append(tags)
            full_syl = self.pinyin.syllables(normalize(name))
            core_syl = self.pinyin.syllables(core)
            self.name_syl.append(full_syl)
            self.core_syl.append(core_syl)
            for tag in tags:
                normalized_tag = normalize(tag)
                if not normalized_tag:
                    continue
                self.tag_display.setdefault(normalized_tag, tag)
                self.tag_index.setdefault(normalized_tag, []).append(index)
                self.tag_syl.setdefault(normalized_tag, self.pinyin.syllables(normalized_tag))

    @classmethod
    def from_config(cls, config: Config, **kwargs: Any) -> "SearchEngine":
        return cls(
            config.root,
            load_catalog(config),
            aliases_path=config.root / "data/search/aliases.json",
            pinyin_path=config.root / "data/search/pinyin.json",
            **kwargs,
        )

    def _resolve_image(self, entry_id: str) -> str | None:
        if not entry_id:
            return None
        for suffix in (".gif", ".jpg"):
            path = self.root / "img" / "converted" / f"{entry_id}{suffix}"
            if path.exists():
                return path.relative_to(self.root).as_posix()
        if self.require_images:
            return None
        return f"img/converted/{entry_id}.jpg"

    def _consider(self, matches: dict[int, Match], candidate: Match) -> None:
        old = matches.get(candidate.index)
        if old is None or (candidate.score, candidate.priority) > (old.score, old.priority):
            matches[candidate.index] = candidate

    def _merge(self, matches: dict[int, Match], index: int, score: float, field: str, value: str, method: str, canonical: str | None = None) -> None:
        self._consider(matches, Match(index, score, field, value, method, canonical))

    def _exact(self, query: str, matches: dict[int, Match]) -> None:
        tag_indices = self.tag_index.get(query, [])
        display = self.tag_display.get(query, query)
        for index in tag_indices:
            self._merge(matches, index, 1.0, "tag", display, "exact_tag")
        for index, norm in enumerate(self.name_norm):
            if norm == query:
                self._merge(matches, index, 0.98, "name", self.entries[index]["name"], "name_exact")
            elif self.core_norm[index] == query:
                self._merge(matches, index, 0.97, "name", self.entries[index]["name"], "name_exact")

    def _alias(self, query: str, matches: dict[int, Match]) -> None:
        expanded: dict[str, tuple[Concept, float, str]] = {}
        for concept, term_weight, term in self.aliases.expand(query):
            old = expanded.get(concept.id)
            if old is None or term_weight > old[1]:
                expanded[concept.id] = (concept, term_weight, term)

        # Homophone typo of a synonym term, e.g. 难锅 -> 难过.
        query_syl = self.pinyin.syllables(query)
        if query_syl:
            for term, concept, term_syl in self.alias_term_pinyin:
                if len(term_syl) != len(query_syl):
                    continue
                similarity = PinyinMap.similarity(query_syl, term_syl)
                if similarity == 1.0:
                    weight = 0.90
                elif similarity >= 0.90:
                    weight = 0.86
                else:
                    continue
                old = expanded.get(concept.id)
                if old is None or weight > old[1]:
                    expanded[concept.id] = (concept, weight, term)

        for concept, term_weight, _term in expanded.values():
            for tag, tag_weight in concept.tags.items():
                score = 0.94 * tag_weight * term_weight
                for index in self.tag_index.get(tag, []):
                    self._merge(
                        matches,
                        index,
                        score,
                        "tag",
                        self.tag_display.get(tag, tag),
                        "alias_tag",
                        concept.id,
                    )
            # Names containing any term of the concept (not only the query term).
            terms = [term for term in concept.terms if len(term) >= 2]
            if not terms:
                continue
            for index, norm in enumerate(self.name_norm):
                core = self.core_norm[index]
                hit = next((term for term in terms if term in norm or term in core), None)
                if hit is not None:
                    self._merge(
                        matches,
                        index,
                        0.86 * term_weight,
                        "name",
                        self.entries[index]["name"],
                        "name_contains",
                        concept.id,
                    )

    def _contains(self, query: str, matches: dict[int, Match]) -> None:
        if len(query) < 2:
            return
        for tag, indices in self.tag_index.items():
            if query in tag:
                for index in indices:
                    self._merge(matches, index, 0.90, "tag", self.tag_display[tag], "tag_contains")
            elif len(tag) >= 2 and tag in query:
                for index in indices:
                    self._merge(matches, index, 0.86, "tag", self.tag_display[tag], "tag_contains")
        for index, norm in enumerate(self.name_norm):
            core = self.core_norm[index]
            if query in norm or query in core:
                self._merge(matches, index, 0.86, "name", self.entries[index]["name"], "name_contains")

    def _pinyin_matches(self, query: str, matches: dict[int, Match]) -> None:
        if len(query) < 2:
            return
        query_syl = self.pinyin.syllables(query)
        if not query_syl:
            return
        for tag, indices in self.tag_index.items():
            tag_syl = self.tag_syl.get(tag)
            if not tag_syl:
                continue
            # A short tag like 右 (you) must not match a long sentence merely
            # because its pinyin is a subsequence.  Tag homophones are useful
            # for same-length words (开新 -> 开心), not for arbitrary prefixes.
            if len(tag_syl) != len(query_syl):
                continue
            similarity = PinyinMap.similarity(query_syl, tag_syl)
            if similarity == 1.0:
                score = 0.93
            elif similarity >= 0.90:
                score = 0.89
            elif similarity >= 0.84:
                score = 0.82
            else:
                continue
            for index in indices:
                self._merge(matches, index, score, "tag", self.tag_display[tag], "pinyin_tag")
        for index in range(len(self.entries)):
            best = 0.0
            for syllables in (self.core_syl[index], self.name_syl[index]):
                if not syllables:
                    continue
                if len(query_syl) > len(syllables) + 1:
                    continue
                similarity = PinyinMap.similarity(query_syl, syllables)
                if similarity == 1.0:
                    best = max(best, 0.89)
                elif similarity >= 0.90:
                    best = max(best, 0.87)
                elif similarity >= 0.85:
                    best = max(best, 0.80)
            if best:
                self._merge(matches, index, best, "name", self.entries[index]["name"], "pinyin_name")

    def _fuzzy(self, query: str, matches: dict[int, Match]) -> None:
        if len(query) < 2:
            return
        query_ngrams = char_ngrams(query)
        for tag, indices in self.tag_index.items():
            ratio = max(sequence_ratio(query, tag), dice(query_ngrams, char_ngrams(tag)))
            if ratio < 0.68:
                continue
            score = 0.70 + 0.14 * ratio
            for index in indices:
                self._merge(matches, index, score, "tag", self.tag_display[tag], "fuzzy_tag")
        for index, core in enumerate(self.core_norm):
            ratio = max(sequence_ratio(query, core), dice(query_ngrams, char_ngrams(core)))
            if ratio < 0.62:
                continue
            score = 0.66 + 0.14 * ratio
            self._merge(matches, index, score, "name", self.entries[index]["name"], "fuzzy_name")

    def _fallback(self, query: str, reason: str, score: float = 0.0, *, seed: int | None = None) -> dict[str, Any]:
        if not self.entries:
            raise SearchError("search index is empty")
        rng = random.Random(seed) if seed is not None else random.SystemRandom()
        index = rng.randrange(len(self.entries))
        return self._result(index, None, query, score, reason, seed=seed)

    def _result(
        self,
        index: int,
        match: Match | None,
        query: str,
        score: float,
        fallback_reason: str | None = None,
        *,
        seed: int | None = None,
    ) -> dict[str, Any]:
        entry = self.entries[index]
        result: dict[str, Any] = {
            "query": query,
            "id": str(entry["id"]),
            "name": entry["name"],
            "tag": list(entry.get("tag", [])),
            "image": self.images[index],
            "matched_field": match.field if match else None,
            "matched_value": match.value if match else None,
            "canonical": match.canonical if match else None,
            "score": round(float(score), 4),
            "method": match.method if match else "fallback",
            "fallback": match is None,
            "candidate_count": 0 if match is None else None,
        }
        if fallback_reason is not None:
            result["fallback_reason"] = fallback_reason
        return result

    def _collect_matches(self, query_norm: str) -> dict[int, Match]:
        matches: dict[int, Match] = {}
        self._exact(query_norm, matches)
        self._alias(query_norm, matches)
        self._contains(query_norm, matches)
        self._pinyin_matches(query_norm, matches)
        self._fuzzy(query_norm, matches)
        return matches

    @staticmethod
    def _sorted_matches(matches: dict[int, Match], entries: list[dict[str, Any]]) -> list[Match]:
        return sorted(
            matches.values(),
            key=lambda match: (-match.score, -match.priority, int(entries[match.index]["id"])),
        )

    def search(
        self,
        query: object,
        *,
        min_score: float = 0.80,
        random_window: float = 0.04,
        seed: int | None = None,
        stable: bool = False,
    ) -> dict[str, Any]:
        raw_query = str(query or "")
        query_norm = normalize(raw_query)
        if not query_norm:
            return self._fallback(raw_query, "empty_query", seed=seed)
        if not 0 <= min_score <= 1:
            raise ValueError("min_score must be between 0 and 1")
        if random_window < 0:
            raise ValueError("random_window cannot be negative")

        matches = self._collect_matches(query_norm)
        if not matches:
            return self._fallback(raw_query, "no_match", seed=seed)
        best_score = max(match.score for match in matches.values())
        if best_score < min_score:
            return self._fallback(raw_query, "below_threshold", best_score, seed=seed)

        top = [
            match
            for match in matches.values()
            if match.score >= best_score - random_window
        ]
        top.sort(key=lambda match: (-match.score, -match.priority, int(self.entries[match.index]["id"])))
        if stable:
            chosen = top[0]
        else:
            rng = random.Random(seed) if seed is not None else random.SystemRandom()
            chosen = rng.choice(top)
        result = self._result(chosen.index, chosen, raw_query, chosen.score)
        result["candidate_count"] = len(top)
        return result

    def search_many(
        self,
        query: object,
        *,
        min_score: float = 0.80,
        limit: int = 40,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Return all matches above min_score, ranked, without random sampling.

        This is the multi-result counterpart of search(). A query with no match
        returns an empty list and fallback=True; callers can then offer the
        random endpoint rather than silently replacing the search results.
        """
        raw_query = str(query or "")
        if not 0 <= min_score <= 1:
            raise ValueError("min_score must be between 0 and 1")
        if limit < 1:
            raise ValueError("limit must be at least 1")
        if offset < 0:
            raise ValueError("offset cannot be negative")
        query_norm = normalize(raw_query)
        base: dict[str, Any] = {
            "query": raw_query,
            "total": 0,
            "items": [],
            "fallback": True,
            "fallback_reason": "empty_query",
            "min_score": min_score,
        }
        if not query_norm:
            return base

        matches = self._collect_matches(query_norm)
        if not matches:
            base["fallback_reason"] = "no_match"
            return base
        best_score = max(match.score for match in matches.values())
        if best_score < min_score:
            base["fallback_reason"] = "below_threshold"
            base["best_score"] = round(float(best_score), 4)
            return base

        ordered = self._sorted_matches(matches, self.entries)
        page = ordered[offset : offset + limit]
        items = [self._result(match.index, match, raw_query, match.score) for match in page]
        return {
            "query": raw_query,
            "total": len(ordered),
            "items": items,
            "fallback": False,
            "fallback_reason": None,
            "min_score": min_score,
            "best_score": round(float(best_score), 4),
        }
