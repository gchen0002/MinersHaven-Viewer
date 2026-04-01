from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .cache import CacheStore, build_index
from .utils import normalize_lookup
from .wiki_client import WikiClient
from .wiki_parser import parse_wiki_page


DEFAULT_CATEGORIES = {
    "Category:Upgrader": "Upgrader",
    "Category:Furnace": "Furnace",
}


@dataclass(slots=True)
class SyncResult:
    total_titles: int
    fetched_count: int
    updated_count: int
    skipped_count: int


class WikiSyncService:
    def __init__(self, store: CacheStore, wiki_client: WikiClient | None = None) -> None:
        self.store = store
        self.wiki = wiki_client or WikiClient()

    def sync_all(self, force_full: bool = False) -> SyncResult:
        snapshot = self.store.load()
        items = dict(snapshot.items)
        previous_revision_map = dict(snapshot.meta.get("revision_map", {}))

        titles_with_types = self._collect_titles_with_types()
        titles = sorted(titles_with_types.keys())
        current_revision_map = self.wiki.get_revision_map(titles)

        run_full_scan = force_full or not items
        stale_titles: list[str] = []
        if run_full_scan:
            stale_titles = titles
        else:
            for title in titles:
                old_rev = previous_revision_map.get(title)
                new_rev = current_revision_map.get(title)
                if old_rev != new_rev:
                    stale_titles.append(title)

        if stale_titles:
            pages = self.wiki.get_pages_wikitext(stale_titles)
            for page in pages:
                item_type = titles_with_types.get(page.title)
                parsed = parse_wiki_page(page, preferred_type=item_type)
                normalized = normalize_lookup(page.title)
                items[normalized] = parsed

        # prune removed pages
        known_norms = {normalize_lookup(title) for title in titles}
        for normalized in list(items.keys()):
            if normalized not in known_norms:
                items.pop(normalized, None)

        revision_map = {
            title: revid for title, revid in current_revision_map.items() if title in titles_with_types
        }

        index = build_index(items)
        meta = dict(snapshot.meta)
        meta.update(
            {
                "revision_map": revision_map,
                "categories": list(DEFAULT_CATEGORIES.keys()),
                "last_sync": datetime.now(tz=timezone.utc).isoformat(),
            }
        )
        if run_full_scan:
            meta["last_full_scan"] = meta["last_sync"]
        self.store.save(items, index, meta)

        return SyncResult(
            total_titles=len(titles),
            fetched_count=len(stale_titles),
            updated_count=len(stale_titles),
            skipped_count=max(0, len(titles) - len(stale_titles)),
        )

    def _collect_titles_with_types(self) -> dict[str, str]:
        combined: dict[str, str] = {}
        for category, item_type in DEFAULT_CATEGORIES.items():
            titles = self.wiki.get_category_members(category)
            for title in titles:
                if title.startswith("Category:"):
                    continue
                combined[title] = item_type
        return combined
