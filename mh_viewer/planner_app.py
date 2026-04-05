from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from .cache import CacheStore
from .calculator import calculate_layout_estimate, parse_inventory_text
from .matcher import ItemMatcher
from .planner_ui import PlannerWindow
from .sync import WikiSyncService


PLANNER_STATE_PATH = Path("data") / "planner_state.json"
AUTO_SAVE_INTERVAL_MS = 15_000
CHANGE_SAVE_DEBOUNCE_MS = 800


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
    window.set_item_catalog(_build_catalog_entries(items))

    def refresh_owned_items_view(inventory_text: str | None = None) -> None:
        parsed = parse_inventory_text(inventory_text if inventory_text is not None else window.get_inventory_text(), matcher)
        window.set_owned_items(_build_owned_item_entries(items, parsed.counts), unknown_entries=parsed.unknown)

    previous_state = _load_planner_state()
    if previous_state:
        window.apply_state(previous_state)
        window.set_status("Restored previous planner session")
    refresh_owned_items_view()

    state_dirty = False
    change_save_job: str | None = None
    periodic_save_job: str | None = None

    def persist_state() -> None:
        nonlocal state_dirty
        _save_planner_state(window.get_state())
        state_dirty = False

    def flush_change_save() -> None:
        nonlocal change_save_job
        change_save_job = None
        if state_dirty:
            persist_state()

    def schedule_periodic_save() -> None:
        nonlocal periodic_save_job
        if state_dirty:
            persist_state()
        periodic_save_job = window.root.after(AUTO_SAVE_INTERVAL_MS, schedule_periodic_save)

    def on_state_changed() -> None:
        nonlocal state_dirty, change_save_job
        state_dirty = True
        refresh_owned_items_view()
        if change_save_job is not None:
            try:
                window.root.after_cancel(change_save_job)
            except Exception:
                pass
        change_save_job = window.root.after(CHANGE_SAVE_DEBOUNCE_MS, flush_change_save)

    def on_close() -> None:
        nonlocal change_save_job, periodic_save_job
        if change_save_job is not None:
            try:
                window.root.after_cancel(change_save_job)
            except Exception:
                pass
            change_save_job = None
        if periodic_save_job is not None:
            try:
                window.root.after_cancel(periodic_save_job)
            except Exception:
                pass
            periodic_save_job = None
        persist_state()

    window.set_close_callback(on_close)
    window.set_change_callback(on_state_changed)
    periodic_save_job = window.root.after(AUTO_SAVE_INTERVAL_MS, schedule_periodic_save)

    def on_calculate() -> None:
        window.sort_inventory_lines()
        inventory_text, target_text, max_mines, use_target, max_upgraders, ban_destroy = window.get_inputs()
        parsed = parse_inventory_text(inventory_text, matcher)
        window.set_owned_items(_build_owned_item_entries(items, parsed.counts), unknown_entries=parsed.unknown)

        if not parsed.counts:
            window.set_status("No valid items parsed")
            return

        result = calculate_layout_estimate(
            items=items,
            counts=parsed.counts,
            max_mines=max_mines,
            target_text=target_text,
            use_target_mode=use_target,
            max_upgraders=max_upgraders,
            allow_destroy_items=not ban_destroy,
        )

        if parsed.unknown:
            result.notes.append(f"Unmatched entries: {len(parsed.unknown)}")

        window.show_result(result)
        if use_target and result.target_eta_overflow:
            window.set_status("Target mode blocked: ETA exceeds 3 minutes; increase total item count")
        else:
            window.set_status(f"Parsed {parsed.inferred_total} item(s), matched {len(parsed.counts)} unique")
        persist_state()

    window.set_run_callback(on_calculate)
    window.run()


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


def _build_catalog_entries(items: dict[str, dict]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for normalized, item in items.items():
        if not isinstance(item, dict):
            continue

        normalized_name = str(normalized).strip()
        if not normalized_name or normalized_name in seen:
            continue
        seen.add(normalized_name)

        name = str(item.get("name") or normalized_name).strip()
        details = item.get("details") or {}
        categories_raw = details.get("categories") if isinstance(details, dict) else None

        categories: list[str] = []
        if isinstance(categories_raw, list):
            for token in categories_raw:
                text = str(token).strip()
                if text:
                    categories.append(text)

        acquisition = item.get("acquisition") or {}
        limited = bool(acquisition.get("limited"))
        rarity_raw = acquisition.get("rarity")
        tags = sorted({*categories, *( ["Limited"] if limited else [])}, key=str.lower)

        tier_raw = str(item.get("tier") or "").strip()
        tier_bucket = _tier_bucket_for_item(tier_raw, tags, limited)
        rarity_sort = _rarity_sort_value(rarity_raw)

        entries.append(
            {
                "name": name,
                "normalized_name": normalized_name,
                "tier": tier_raw,
                "tier_bucket": tier_bucket,
                "rarity": rarity_raw,
                "rarity_sort": rarity_sort,
                "limited": limited,
                "tags": tags,
            }
        )

    entries.sort(
        key=lambda entry: (
            _tier_bucket_rank(str(entry.get("tier_bucket") or "other")),
            entry.get("rarity_sort") is None,
            entry.get("rarity_sort") if entry.get("rarity_sort") is not None else float("inf"),
            str(entry.get("name") or "").lower(),
        )
    )
    return entries


def _build_owned_item_entries(items: dict[str, dict], counts: dict[str, int]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []

    for normalized_name, quantity in counts.items():
        count = int(quantity)
        if count <= 0:
            continue

        item = items.get(normalized_name)
        if not isinstance(item, dict):
            continue

        details = item.get("details") if isinstance(item.get("details"), dict) else {}
        categories_raw = details.get("categories") if isinstance(details, dict) else None
        categories: list[str] = []
        if isinstance(categories_raw, list):
            for token in categories_raw:
                text = str(token).strip()
                if text:
                    categories.append(text)
        categories_norm = {token.lower() for token in categories}
        is_portable = any("portable" in token for token in categories_norm)

        acquisition = item.get("acquisition") if isinstance(item.get("acquisition"), dict) else {}
        limited = bool(acquisition.get("limited"))
        tier = str(item.get("tier") or "-")
        tier_bucket = _owned_tier_bucket_for_item(tier, categories, limited)
        rarity_raw = acquisition.get("rarity")
        rarity_sort = _rarity_sort_value(rarity_raw)

        mpu_payload = item.get("mpu") if isinstance(item.get("mpu"), dict) else {}
        multiplier_payload = item.get("multiplier") if isinstance(item.get("multiplier"), dict) else {}

        mpu_value = _as_float(mpu_payload.get("value") if isinstance(mpu_payload, dict) else None)
        multiplier_value = _as_float(multiplier_payload.get("value") if isinstance(multiplier_payload, dict) else None)

        mpu_text = str((mpu_payload or {}).get("text") or "-")
        if mpu_text == "-" and mpu_value is not None:
            mpu_text = f"x{mpu_value:g}"

        multiplier_text = str((multiplier_payload or {}).get("text") or "-")
        if multiplier_text == "-" and multiplier_value is not None:
            multiplier_text = f"x{multiplier_value:g}"

        entries.append(
            {
                "normalized_name": normalized_name,
                "name": str(item.get("name") or normalized_name),
                "quantity": count,
                "mpu_value": mpu_value,
                "mpu_text": mpu_text,
                "multiplier_value": multiplier_value,
                "multiplier_text": multiplier_text,
                "tier": tier,
                "tier_bucket": tier_bucket,
                "item_type": str(item.get("type") or "-"),
                "rarity": rarity_raw,
                "rarity_sort": rarity_sort,
                "wiki_url": str(item.get("wiki_url") or ""),
                "wiki_title": str((item.get("refs") or {}).get("wiki_title") or item.get("name") or normalized_name),
                "is_portable": is_portable,
                "description": str(item.get("description") or ""),
                "how_to_use": str(item.get("how_to_use") or ""),
                "size": item.get("size") if isinstance(item.get("size"), dict) else {},
                "proof_and_limits": item.get("proof_and_limits") if isinstance(item.get("proof_and_limits"), dict) else {},
                "acquisition": acquisition,
                "effect_tags": item.get("effect_tags") if isinstance(item.get("effect_tags"), dict) else {},
                "drop_rate": item.get("drop_rate") if isinstance(item.get("drop_rate"), dict) else {},
                "ore_worth": item.get("ore_worth") if isinstance(item.get("ore_worth"), dict) else {},
            }
        )

    entries.sort(key=_owned_item_sort_key)
    return entries


def _owned_item_sort_key(entry: dict[str, Any]) -> tuple[bool, float, str]:
    tier_bucket = str(entry.get("tier_bucket") or "other")
    rarity_sort = _as_float(entry.get("rarity_sort"))
    return (
        _owned_tier_bucket_rank(tier_bucket),
        rarity_sort is None,
        rarity_sort if rarity_sort is not None else float("inf"),
        str(entry.get("name") or "").lower(),
    )


def _owned_tier_bucket_for_item(tier_raw: str, categories: list[str], limited: bool) -> str:
    tier_norm = tier_raw.lower()
    tag_norm = {token.lower() for token in categories}

    is_advanced_reborn = any(
        token in tier_norm or token in tag_norm
        for token in ["advanced reborn", "adv reborn", "evolved reborn"]
    )
    is_reborn = ("reborn" in tier_norm) or any("reborn" in token for token in tag_norm)
    is_limited = limited or ("limited" in tag_norm)

    if is_limited and is_reborn:
        return "limited_reborn"
    if "eggxotic" in tier_norm or "eggxotic" in tag_norm:
        return "eggxotic"
    if "contraband" in tier_norm or "contraband" in tag_norm:
        return "contraband"
    if "luxury" in tier_norm or "luxury" in tag_norm:
        return "luxury"
    if "exotic" in tier_norm or "exotic" in tag_norm:
        return "exotic"
    if is_advanced_reborn:
        return "advanced_reborn"
    if is_reborn:
        return "reborn"
    if "collectible" in tier_norm or "collectible" in tag_norm:
        return "collectible"
    if "premium" in tier_norm or "premium" in tag_norm:
        return "premium"
    return "other"


def _owned_tier_bucket_rank(bucket: str) -> int:
    order = {
        "limited_reborn": 0,
        "eggxotic": 1,
        "contraband": 2,
        "luxury": 3,
        "exotic": 4,
        "advanced_reborn": 5,
        "reborn": 6,
        "collectible": 7,
        "premium": 8,
        "other": 9,
    }
    return order.get(bucket, 9)


def _rarity_sort_value(raw: Any) -> float | None:
    numeric = _as_float(raw)
    if numeric is not None:
        return numeric

    text = str(raw or "").strip().lower()
    if not text:
        return None

    number_match = re.search(r"([0-9]+(?:\.[0-9]+)?)", text)
    if number_match:
        try:
            return float(number_match.group(1))
        except ValueError:
            pass

    named_scale = {
        "common": 1,
        "uncommon": 2,
        "rare": 3,
        "epic": 4,
        "legendary": 5,
        "mythic": 6,
        "mythical": 6,
        "sacred": 7,
    }
    for token, value in named_scale.items():
        if token in text:
            return float(value)

    return None


def _as_float(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    return None


def _tier_bucket_for_item(tier_raw: str, tags: list[str], limited: bool) -> str:
    tier_norm = tier_raw.lower()
    tag_norm = {token.lower() for token in tags}

    is_advanced_reborn = any(
        token in tier_norm or token in tag_norm
        for token in ["advanced reborn", "adv reborn", "evolved reborn"]
    )
    is_reborn_family = ("reborn" in tier_norm) or any("reborn" in token for token in tag_norm)

    is_limited = limited or ("limited" in tag_norm)

    if is_limited and is_reborn_family:
        return "limited_reborn"
    if "eggxotic" in tier_norm or "eggxotic" in tag_norm:
        return "eggxotic"
    if "contraband" in tier_norm or "contraband" in tag_norm:
        return "contraband"
    if "luxury" in tier_norm or "luxury" in tag_norm:
        return "luxury"
    if "exotic" in tier_norm or "exotic" in tag_norm:
        return "exotic"
    if is_advanced_reborn:
        return "advanced_reborn"
    if is_reborn_family:
        return "reborn"
    if "collectible" in tier_norm or "collectible" in tag_norm:
        return "collectible"
    if "premium" in tier_norm or "premium" in tag_norm:
        return "premium"
    return "other"


def _tier_bucket_rank(bucket: str) -> int:
    order = {
        "limited_reborn": 0,
        "eggxotic": 1,
        "contraband": 2,
        "luxury": 3,
        "exotic": 4,
        "advanced_reborn": 5,
        "reborn": 6,
        "collectible": 7,
        "premium": 8,
        "other": 9,
    }
    return order.get(bucket, 9)


if __name__ == "__main__":
    run()
