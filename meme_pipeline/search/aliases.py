from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .normalize import normalize


@dataclass(frozen=True)
class Concept:
    id: str
    label: str
    terms: tuple[str, ...]
    tags: dict[str, float]


class AliasTable:
    """Load the manually-reviewable concept table from data/search/aliases.json."""

    def __init__(self, path: Path | None) -> None:
        self.concepts: dict[str, Concept] = {}
        self.term_map: dict[str, list[str]] = {}
        if path and path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                payload = {}
            raw_concepts = payload.get("concepts") if isinstance(payload, dict) else None
            if isinstance(raw_concepts, list):
                for item in raw_concepts:
                    if not isinstance(item, dict):
                        continue
                    concept_id = str(item.get("id", "")).strip()
                    if not concept_id:
                        continue
                    terms = []
                    for term in item.get("terms", []):
                        normalized = normalize(term)
                        if len(normalized) >= 1 and normalized not in terms:
                            terms.append(normalized)
                    tags: dict[str, float] = {}
                    raw_tags = item.get("tags", {})
                    if isinstance(raw_tags, dict):
                        for tag, weight in raw_tags.items():
                            normalized_tag = normalize(tag)
                            if not normalized_tag:
                                continue
                            try:
                                value = float(weight)
                            except (TypeError, ValueError):
                                continue
                            if 0 < value <= 1:
                                tags[normalized_tag] = value
                    concept = Concept(
                        id=concept_id,
                        label=str(item.get("label", concept_id)),
                        terms=tuple(terms),
                        tags=tags,
                    )
                    self.concepts[concept_id] = concept
                    for term in terms:
                        self.term_map.setdefault(term, []).append(concept_id)

    def expand(self, query_norm: str) -> list[tuple[Concept, float, str]]:
        """Return (concept, term_weight, matched_term) candidates for a query."""
        if not query_norm:
            return []
        matches: dict[str, tuple[Concept, float, str]] = {}
        for term, concept_ids in self.term_map.items():
            if term == query_norm:
                weight = 1.0
            elif len(query_norm) >= 2 and len(term) >= 2 and (query_norm in term or term in query_norm):
                weight = 0.88
            else:
                continue
            for concept_id in concept_ids:
                concept = self.concepts.get(concept_id)
                if concept is None:
                    continue
                old = matches.get(concept_id)
                if old is None or weight > old[1]:
                    matches[concept_id] = (concept, weight, term)
        return sorted(matches.values(), key=lambda item: (-item[1], item[0].id))
