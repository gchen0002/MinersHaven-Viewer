from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rapidfuzz import fuzz, process

from .utils import normalize_lookup


@dataclass(slots=True)
class MatchResult:
    normalized_name: str
    score: int
    alias: str
    query: str


class ItemMatcher:
    def __init__(self, alias_map: dict[str, list[str]]) -> None:
        self.alias_map = alias_map
        self.aliases = list(alias_map.keys())

    def is_empty(self) -> bool:
        return not self.aliases

    @classmethod
    def from_index(cls, index_payload: dict[str, Any]) -> "ItemMatcher":
        alias_map_raw = index_payload.get("aliases", {})
        alias_map: dict[str, list[str]] = {}
        if isinstance(alias_map_raw, dict):
            for key, values in alias_map_raw.items():
                if not isinstance(key, str):
                    continue
                out_values: list[str] = []
                if isinstance(values, list):
                    for value in values:
                        if isinstance(value, str):
                            out_values.append(value)
                if out_values:
                    alias_map[key] = out_values
        return cls(alias_map)

    def match(self, text: str, min_score: int = 78) -> MatchResult | None:
        query = normalize_lookup(text)
        if not query:
            return None

        direct = self.alias_map.get(query)
        if direct:
            return MatchResult(normalized_name=direct[0], score=100, alias=query, query=query)

        alias, score = self.probe(query)
        if not alias or score < min_score:
            return None

        names = self.alias_map.get(alias)
        if not names:
            return None
        return MatchResult(normalized_name=names[0], score=score, alias=alias, query=query)

    def probe(self, text: str) -> tuple[str | None, int]:
        query = normalize_lookup(text)
        if not query:
            return (None, 0)

        best_alias: str | None = None
        best_score = 0.0

        candidates = self._candidate_queries(query)
        for candidate in candidates:
            result = process.extractOne(candidate, self.aliases, scorer=fuzz.WRatio)
            if not result:
                continue
            alias, score, _ = result
            if score > best_score:
                best_alias = str(alias)
                best_score = float(score)

        return (best_alias, int(round(best_score)))

    def _candidate_queries(self, query: str) -> list[str]:
        queries: list[str] = [query]
        tokens = query.split()
        if len(tokens) <= 1:
            return queries

        max_window = min(4, len(tokens))
        seen: set[str] = {query}
        for window in range(1, max_window + 1):
            for start in range(0, len(tokens) - window + 1):
                part = " ".join(tokens[start : start + window]).strip()
                if len(part) < 3 or part in seen:
                    continue
                queries.append(part)
                seen.add(part)

        return queries
