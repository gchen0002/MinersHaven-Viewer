from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .cache import CacheStore
from .calculator import calculate_layout_estimate, parse_inventory_text
from .matcher import ItemMatcher
from .planner_ui import PlannerWindow
from .sync import WikiSyncService


PLANNER_STATE_PATH = Path("data") / "planner_state.json"


def run(argv: list[str] | None = None) -> None:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    store = CacheStore("data")
    sync_service = WikiSyncService(store)

    if args.sync or args.sync_only:
        result = sync_service.sync_all(force_full=args.full)
        print(
            f"Sync complete: total={result.total_titles} fetched={result.fetched_count} "
            f"skipped={result.skipped_count}"
        )
        if args.sync_only:
            return

    snapshot = store.load()
    if not snapshot.items:
        print("Cache is empty; running full sync...")
        sync_service.sync_all(force_full=True)
        snapshot = store.load()

    if not snapshot.items:
        print("No cached items found; exiting")
        sys.exit(1)

    matcher = ItemMatcher.from_index(snapshot.index)
    if matcher.is_empty():
        print("Index is empty; run with --sync --full")
        sys.exit(1)

    run_planner_ui(snapshot.items, matcher)


def run_planner_ui(items: dict[str, dict], matcher: ItemMatcher) -> None:
    window = PlannerWindow()
    catalog_names = sorted(
        {
            str(item.get("name") or normalized)
            for normalized, item in items.items()
            if isinstance(item, dict)
        }
    )
    window.set_item_catalog(catalog_names)

    previous_state = _load_planner_state()
    if previous_state:
        window.apply_state(previous_state)
        window.set_status("Restored previous planner session")

    def on_close() -> None:
        _save_planner_state(window.get_state())

    window.set_close_callback(on_close)

    def on_calculate() -> None:
        inventory_text, target_text, max_mines, use_target, loop_cap, ban_destroy = window.get_inputs()
        parsed = parse_inventory_text(inventory_text, matcher)

        if not parsed.counts:
            window.set_status("No valid items parsed")
            return

        result = calculate_layout_estimate(
            items=items,
            counts=parsed.counts,
            max_mines=max_mines,
            target_text=target_text,
            use_target_mode=use_target,
            loop_cap=loop_cap,
            allow_destroy_items=not ban_destroy,
        )

        if parsed.unknown:
            result.notes.append(f"Unmatched entries: {len(parsed.unknown)}")

        window.show_result(result)
        window.set_status(f"Parsed {parsed.inferred_total} item(s), matched {len(parsed.counts)} unique")
        _save_planner_state(window.get_state())

    window.set_run_callback(on_calculate)

    try:
        while True:
            window.tick()
    except Exception:
        return


def _load_planner_state() -> dict:
    if not PLANNER_STATE_PATH.exists():
        return {}
    try:
        payload = json.loads(PLANNER_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_planner_state(state: dict[str, object]) -> None:
    try:
        PLANNER_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        PLANNER_STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError:
        pass


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Miners Haven planner calculator UI")
    parser.add_argument("--sync", action="store_true", help="Sync cache before launching planner")
    parser.add_argument("--sync-only", action="store_true", help="Only sync cache and exit")
    parser.add_argument("--full", action="store_true", help="Force full refresh from wiki")
    parser.add_argument("--config", default=str(Path("config.json")), help="Unused placeholder for compatibility")
    return parser


if __name__ == "__main__":
    run()
