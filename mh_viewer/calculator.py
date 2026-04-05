from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .matcher import ItemMatcher
from .utils import normalize_lookup


UNIT_ORDER = [
    "",
    "k",
    "m",
    "b",
    "t",
    "qd",
    "qn",
    "sx",
    "sp",
    "oc",
    "no",
    "de",
    "ud",
    "dd",
    "td",
    "qad",
    "qid",
    "sxd",
    "spd",
    "od",
    "nd",
    "vg",
    "uvg",
    "dvg",
]
UNIT_RANK = {unit: index for index, unit in enumerate(UNIT_ORDER)}
UNIT_ALIASES = {
    "qa": "qd",
    "qi": "qn",
    "dc": "de",
    "n": "no",
}


@dataclass(slots=True)
class InventoryParseResult:
    counts: dict[str, int]
    unknown: list[str]
    inferred_total: int


@dataclass(slots=True)
class MinePick:
    normalized_name: str
    name: str
    ore_per_second: float | None
    base_ore_value: float | None
    expected_value_per_second: float
    confidence: float
    tile_footprint: int


@dataclass(slots=True)
class UpgraderPick:
    normalized_name: str
    name: str
    item_type: str
    is_portable: bool
    is_blaster: bool
    multiplier: float
    conditional_fire_multiplier: float | None
    overheated_multiplier: float | None
    applies_fire: bool
    extinguishes_fire: bool
    confidence: float
    mpu_cap: float | None
    is_resetter: bool
    destroys_ore: bool
    is_furnace: bool
    is_cell_furnace: bool
    is_teleporter: bool
    is_splitter: bool
    is_merger: bool
    directional: bool
    randomized: bool
    requires_condition: bool
    excludes_condition: bool
    works_with: bool
    status_effects: tuple[str, ...]
    unmodeled_effects: tuple[str, ...]
    related_items: tuple[str, ...]
    throughput_multiplier: float | None
    tile_footprint: int
    first_only: bool
    max_uses: int | None


_OVERRIDES_PATH = Path(__file__).with_name("calculator_overrides.json")
_DEFAULT_OVERRIDES: dict[str, dict[str, Any]] = {
    "robotic enhancer": {
        "base_multiplier": 6.0,
    },
    "cooling chamber": {
        "base_multiplier": 7.0,
        "conditional_fire_multiplier": 11.0,
        "overheated_multiplier": 5.0,
        "force_applies_fire": False,
        "force_extinguishes_fire": True,
    },
    "obsidian infusion device": {
        "base_multiplier": 2.0,
        "conditional_fire_multiplier": 12.0,
    },
}


def _load_multiplier_overrides() -> tuple[dict[str, dict[str, Any]], str]:
    table: dict[str, dict[str, Any]] = {
        key: dict(value) for key, value in _DEFAULT_OVERRIDES.items()
    }
    version = "builtin"
    try:
        payload = json.loads(_OVERRIDES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return table, version

    if not isinstance(payload, dict):
        return table, version

    raw_items = payload.get("items")
    if isinstance(raw_items, dict):
        for raw_name, raw_values in raw_items.items():
            normalized_name = normalize_lookup(str(raw_name))
            if not normalized_name or not isinstance(raw_values, dict):
                continue

            parsed: dict[str, Any] = {}
            for field in ["base_multiplier", "conditional_fire_multiplier", "overheated_multiplier"]:
                value = raw_values.get(field)
                if isinstance(value, (int, float)):
                    parsed[field] = float(value)
                elif isinstance(value, str):
                    try:
                        parsed[field] = float(value)
                    except ValueError:
                        continue
            for field in ["force_applies_fire", "force_extinguishes_fire"]:
                value = raw_values.get(field)
                if isinstance(value, bool):
                    parsed[field] = value
            for field in ["first_only", "disabled"]:
                value = raw_values.get(field)
                if isinstance(value, bool):
                    parsed[field] = value
            value = raw_values.get("max_uses")
            if isinstance(value, (int, float)):
                parsed["max_uses"] = int(value)
            elif isinstance(value, str):
                try:
                    parsed["max_uses"] = int(float(value))
                except ValueError:
                    pass
            reason = raw_values.get("reason")
            if isinstance(reason, str) and reason.strip():
                parsed["reason"] = reason.strip()
            if parsed:
                table[normalized_name] = parsed

    if isinstance(payload.get("version"), str) and payload.get("version"):
        version = str(payload.get("version"))
    return table, version


OVERRIDE_MULTIPLIER_TABLE, OVERRIDE_RULESET_VERSION = _load_multiplier_overrides()
UNMODELED_STATUS_EFFECTS = {"anti_gravity"}
RESETTER_BASE_EFFICIENCY = 0.82
RESETTER_EFFICIENCY_STEP = 0.025
RESETTER_EFFICIENCY_MAX = 0.95
REQUIRES_CONDITION_HIT_MULT = 1.03
REQUIRES_CONDITION_MISS_MULT = 0.82
EXCLUDES_CONDITION_HIT_MULT = 0.9
EXCLUDES_CONDITION_MISS_MULT = 1.01
STATUS_CONFLICT_MULT = 0.92
MAX_TARGET_ETA_SECONDS = 180
TARGET_BEAM_WIDTH = 14
TARGET_BEAM_BRANCH = 10
TARGET_BEAM_POOL_LIMIT = 56
TARGET_BEAM_MAX_EVALUATIONS = 6000
TARGET_SEARCH_ITEM_PENALTY_SECONDS = 2.5


@dataclass(slots=True)
class _TargetEval:
    estimated_total_vps: float
    target_seconds: float | None
    single_ore_value: float
    score: float
    item_count: int


@dataclass(slots=True)
class _TargetBeamState:
    use_counts: tuple[int, ...]
    chain: tuple[UpgraderPick, ...]
    item_count: int
    resetters: int
    teleporters: int
    splitters: int
    mergers: int
    destroy_count: int
    requires_condition_count: int
    first_only_selected: bool
    final_upgrader_uses: int
    eval_result: _TargetEval


@dataclass(slots=True)
class _TargetCandidate:
    pick: UpgraderPick
    max_available: int


@dataclass(slots=True)
class ProgressionOpportunity:
    target_name: str
    target_tier: str
    target_type: str
    required_total: int
    owned_total: int
    closeness: float
    ready_now: bool
    ingredient_names: tuple[str, ...]
    missing_names: tuple[str, ...]


@dataclass(slots=True)
class CalculationResult:
    mine_picks: list[MinePick]
    upgrader_picks: list[UpgraderPick]
    progression_opportunities: list[ProgressionOpportunity]
    mine_expected_value_per_second: float
    base_ores_per_second: float
    effective_ores_per_second: float
    bottleneck_multiplier: float
    estimated_multiplier: float
    estimated_total_value_per_second: float
    loop_passes: int
    mpu_constrained: bool
    mpu_effective_cap: float | None
    conveyor_constrained: bool
    dimension_penalty: float
    tile_limit: int
    tile_ratio: float
    limiter_recommendation: str
    selected_furnace: str | None
    phase_breakdown: dict[str, list[str]]
    synergy_score: float
    target_seconds: float | None
    target_value: float | None
    single_ore_base_value: float | None
    single_ore_estimated_value: float | None
    min_items_to_target: int | None
    min_items_target_item: str | None
    target_too_small: bool
    target_eta_overflow: bool
    used_tiles: int
    notes: list[str]


def parse_inventory_text(text: str, matcher: ItemMatcher) -> InventoryParseResult:
    counts: dict[str, int] = {}
    unknown: list[str] = []
    inferred_total = 0

    chunks = [part.strip() for part in re.split(r"[\n,]", text) if part.strip()]
    for chunk in chunks:
        name, count = _split_name_count(chunk)
        inferred_total += count

        resolved = _resolve_name(name, matcher)
        if not resolved:
            unknown.append(chunk)
            continue
        counts[resolved] = counts.get(resolved, 0) + count

    return InventoryParseResult(counts=counts, unknown=unknown, inferred_total=inferred_total)


def calculate_layout_estimate(
    items: dict[str, dict[str, Any]],
    counts: dict[str, int],
    max_mines: int,
    target_text: str,
    use_target_mode: bool,
    loop_cap: int = 4,
    max_upgraders: int = 20,
    tile_limit: int = 3844,
    allow_destroy_items: bool = True,
) -> CalculationResult:
    notes: list[str] = []
    target_value = parse_cash_amount(target_text) if use_target_mode else None
    if use_target_mode and target_value is None:
        notes.append("Target amount could not be parsed; accepted example: 7 de")

    mine_candidates = _collect_mine_candidates(items, counts)
    if not mine_candidates:
        notes.append("No mine with usable throughput profile found in inventory input")
        return CalculationResult(
            mine_picks=[],
            upgrader_picks=[],
            progression_opportunities=[],
            mine_expected_value_per_second=0.0,
            base_ores_per_second=0.0,
            effective_ores_per_second=0.0,
            bottleneck_multiplier=1.0,
            estimated_multiplier=1.0,
            estimated_total_value_per_second=0.0,
            loop_passes=1,
            mpu_constrained=False,
            mpu_effective_cap=None,
            conveyor_constrained=False,
            dimension_penalty=1.0,
            tile_limit=max(1, int(tile_limit)),
            tile_ratio=0.0,
            limiter_recommendation="Add mines/upgraders first; no active limiter yet",
            selected_furnace=None,
            phase_breakdown={"pre": [], "core": [], "post": [], "output": []},
            synergy_score=1.0,
            target_seconds=None,
            target_value=target_value,
            single_ore_base_value=None,
            single_ore_estimated_value=None,
            min_items_to_target=None,
            min_items_target_item=None,
            target_too_small=False,
            target_eta_overflow=False,
            used_tiles=0,
            notes=notes,
        )

    mine_candidates.sort(key=lambda item: item.expected_value_per_second * item.confidence, reverse=True)
    selected_mines = mine_candidates[: max(1, min(3, int(max_mines)))]

    upgrader_candidates, excluded_cell_furnaces, excluded_disabled, excluded_antigravity = _collect_upgrader_candidates(items, counts)
    if excluded_cell_furnaces > 0:
        notes.append(
            f"Excluded {excluded_cell_furnaces} cell furnace item(s) from upgrader chain (raw-ore only behavior)"
        )
    if excluded_disabled > 0:
        notes.append(f"Excluded {excluded_disabled} override-disabled item(s) from calculator")
    if excluded_antigravity > 0:
        notes.append(f"Excluded {excluded_antigravity} anti-gravity conveyor upgrader candidate(s)")
    furnace_candidates = _collect_furnace_candidates(items, counts, allow_destroy_items=allow_destroy_items)

    mine_vps = sum(item.expected_value_per_second for item in selected_mines)
    base_ores_per_second = sum(item.ore_per_second or 0.0 for item in selected_mines)
    single_ore_base_value = _estimate_single_ore_base_value(selected_mines, mine_vps, base_ores_per_second)

    if use_target_mode and target_value is not None:
        selected_upgraders, selected_furnace, legality_notes = _select_target_upgrader_chain(
            candidates=upgrader_candidates,
            furnace_candidates=furnace_candidates,
            max_items=max_upgraders,
            loop_cap=loop_cap,
            allow_destroy_items=allow_destroy_items,
            mine_vps=mine_vps,
            base_ores_per_second=base_ores_per_second,
            base_single_ore_value=single_ore_base_value,
            target_value=target_value,
            tile_limit=tile_limit,
            mine_tile_footprint=sum(item.tile_footprint for item in selected_mines),
        )
    else:
        selected_upgraders, selected_furnace, legality_notes = _select_legal_upgrader_chain(
            upgrader_candidates,
            furnace_candidates=furnace_candidates,
            max_items=max_upgraders,
            loop_cap=loop_cap,
            allow_destroy_items=allow_destroy_items,
        )
    notes.extend(legality_notes)

    bottleneck_multiplier, bottleneck_notes = _estimate_ordered_bottleneck(selected_upgraders)
    notes.extend(bottleneck_notes)

    effective_ores_per_second = base_ores_per_second
    if base_ores_per_second > 0:
        effective_ores_per_second = base_ores_per_second * min(1.0, bottleneck_multiplier)
    throughput_ratio = 1.0 if base_ores_per_second <= 0 else max(0.05, effective_ores_per_second / base_ores_per_second)
    adjusted_mine_vps = mine_vps * throughput_ratio

    estimated_multiplier, loop_passes, stage_notes, uncapped_multiplier, mpu_cap = _simulate_ordered_pipeline(
        selected_upgraders,
        loop_cap,
    )
    notes.extend(stage_notes)

    synergy_multiplier, synergy_notes = _evaluate_chain_synergy(selected_upgraders)
    notes.extend(synergy_notes)
    estimated_multiplier *= synergy_multiplier

    destruction_penalty = _destruction_penalty(selected_upgraders)
    if destruction_penalty < 1.0:
        notes.append("Applied ore-destruction reliability penalty")

    estimated_total_vps = adjusted_mine_vps * estimated_multiplier * destruction_penalty

    used_tiles = sum(item.tile_footprint for item in selected_mines) + sum(item.tile_footprint for item in selected_upgraders)
    tile_limit = max(1, int(tile_limit))
    tile_ratio = used_tiles / tile_limit
    dimension_penalty = _dimension_penalty(tile_ratio)
    estimated_total_vps *= dimension_penalty

    if bottleneck_multiplier < 1.0:
        notes.append(f"Conveyor bottleneck detected at x{bottleneck_multiplier:.2f}")
    elif bottleneck_multiplier > 1.0:
        notes.append(f"Conveyor capacity above source rate (x{bottleneck_multiplier:.2f})")

    mpu_factor = 1.0 if uncapped_multiplier <= 0 else max(0.0, min(1.0, estimated_multiplier / uncapped_multiplier))
    conveyor_factor = max(0.0, min(1.0, throughput_ratio))
    if conveyor_factor < mpu_factor:
        notes.append("Conveyor speed is currently the stronger limiter than MPU")
    elif mpu_factor < conveyor_factor:
        notes.append("MPU cap is currently the stronger limiter than conveyor speed")
    else:
        notes.append("MPU and conveyor limits are currently balanced")

    if dimension_penalty < 1.0:
        notes.append(f"Dimension pressure penalty applied at x{dimension_penalty:.2f} (tiles {used_tiles}/{tile_limit})")

    limiter_recommendation = _build_limiter_recommendation(
        mpu_factor=mpu_factor,
        conveyor_factor=conveyor_factor,
        dimension_factor=dimension_penalty,
        destruction_factor=destruction_penalty,
    )

    dynamic_mines = sum(1 for item in selected_mines if item.confidence < 0.5)
    if dynamic_mines:
        notes.append(f"{dynamic_mines} selected mine(s) rely on dynamic value formulas")

    unmodeled_effects = _collect_unmodeled_effects(selected_upgraders)
    if unmodeled_effects:
        notes.append(f"Ignored unmodeled effects in estimate: {', '.join(unmodeled_effects)}")

    ordered_upgraders = _order_chain_for_pipeline(selected_upgraders)
    phases = _split_pipeline_phases(ordered_upgraders)
    phase_breakdown = {
        "pre": [item.name for item in phases["pre"]],
        "core": [item.name for item in phases["core"]],
        "post": [item.name for item in phases["post"]],
        "output": [item.name for item in phases["output"]],
    }

    single_ore_estimated_value: float | None = None
    if single_ore_base_value is not None:
        single_ore_estimated_value = max(0.0, single_ore_base_value * estimated_multiplier * destruction_penalty)

    target_seconds: float | None = None
    min_items_to_target: int | None = None
    min_items_target_item: str | None = None
    target_too_small = False
    target_eta_overflow = False

    if use_target_mode and target_value is not None:
        if single_ore_base_value is not None and target_value <= single_ore_base_value:
            target_too_small = True
            min_items_to_target = 0
            notes.append("Target is at or below base single-ore value; skipped ETA calculation")
        else:
            if single_ore_base_value is not None:
                min_items_to_target, min_items_target_item = _minimum_items_for_single_ore_target(
                    base_single_ore_value=single_ore_base_value,
                    target_value=target_value,
                    ordered_upgraders=ordered_upgraders,
                    loop_cap=loop_cap,
                )
                if min_items_to_target is None:
                    notes.append("Selected chain does not reach target on a single-ore basis")

            if estimated_total_vps > 0:
                raw_eta = target_value / estimated_total_vps
                if raw_eta > MAX_TARGET_ETA_SECONDS:
                    target_eta_overflow = True
                    notes.append("ETA exceeds 3 minutes; blocked by target-time policy")
                else:
                    target_seconds = raw_eta

    return CalculationResult(
        mine_picks=selected_mines,
        upgrader_picks=ordered_upgraders,
        progression_opportunities=_build_progression_opportunities(items, counts),
        mine_expected_value_per_second=mine_vps,
        base_ores_per_second=base_ores_per_second,
        effective_ores_per_second=effective_ores_per_second,
        bottleneck_multiplier=bottleneck_multiplier,
        estimated_multiplier=estimated_multiplier,
        estimated_total_value_per_second=max(0.0, estimated_total_vps),
        loop_passes=loop_passes,
        mpu_constrained=mpu_cap is not None,
        mpu_effective_cap=mpu_cap,
        conveyor_constrained=bottleneck_multiplier < 1.0,
        dimension_penalty=dimension_penalty,
        tile_limit=tile_limit,
        tile_ratio=tile_ratio,
        limiter_recommendation=limiter_recommendation,
        selected_furnace=selected_furnace.name if selected_furnace is not None else None,
        phase_breakdown=phase_breakdown,
        synergy_score=synergy_multiplier,
        target_seconds=target_seconds,
        target_value=target_value,
        single_ore_base_value=single_ore_base_value,
        single_ore_estimated_value=single_ore_estimated_value,
        min_items_to_target=min_items_to_target,
        min_items_target_item=min_items_target_item,
        target_too_small=target_too_small,
        target_eta_overflow=target_eta_overflow,
        used_tiles=used_tiles,
        notes=notes,
    )


def parse_cash_amount(text: str) -> float | None:
    cleaned = str(text).lower().replace(",", " ").strip()
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*([a-z]{0,4})", cleaned)
    if not match:
        return None

    amount = float(match.group(1))
    unit = normalize_lookup(match.group(2)).replace(" ", "")
    unit = UNIT_ALIASES.get(unit, unit)
    if unit not in UNIT_RANK:
        return None
    return amount * (1000**UNIT_RANK[unit])


def format_cash_amount(value: float) -> str:
    if value < 0:
        return "0"
    if value == 0:
        return "0"

    rank = int(math.floor(math.log(value, 1000))) if value >= 1000 else 0
    rank = max(0, min(rank, len(UNIT_ORDER) - 1))
    scaled = value / (1000**rank)
    if scaled >= 100:
        text = f"{scaled:.0f}"
    elif scaled >= 10:
        text = f"{scaled:.1f}".rstrip("0").rstrip(".")
    else:
        text = f"{scaled:.2f}".rstrip("0").rstrip(".")
    unit = UNIT_ORDER[rank]
    return f"{text} {unit}".strip()


def format_duration(seconds: float) -> str:
    if seconds < 1:
        return "<1s"
    total = int(round(seconds))
    days, remainder = divmod(total, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, sec = divmod(remainder, 60)
    if days > 0:
        return f"{days}d {hours}h {minutes}m"
    if hours > 0:
        return f"{hours}h {minutes}m {sec}s"
    if minutes > 0:
        return f"{minutes}m {sec}s"
    return f"{sec}s"


def _collect_mine_candidates(items: dict[str, dict[str, Any]], counts: dict[str, int]) -> list[MinePick]:
    mine_candidates: list[MinePick] = []
    for normalized_name, count in counts.items():
        item = items.get(normalized_name)
        if not item or normalize_lookup(str(item.get("type") or "")) != "dropper":
            continue

        replicated = min(max(0, count), 20)
        for _ in range(replicated):
            pick = _mine_pick_from_item(normalized_name, item)
            if pick is not None:
                mine_candidates.append(pick)
    return mine_candidates


def _collect_upgrader_candidates(
    items: dict[str, dict[str, Any]],
    counts: dict[str, int],
) -> tuple[list[UpgraderPick], int, int, int]:
    candidates: list[UpgraderPick] = []
    excluded_cell_furnaces = 0
    excluded_disabled = 0
    excluded_antigravity = 0
    for normalized_name, count in counts.items():
        item = items.get(normalized_name)
        if not item:
            continue
        item_type = normalize_lookup(str(item.get("type") or ""))
        if item_type not in {"upgrader", "furnace"}:
            continue

        if _is_antigravity_conveyor_item(item):
            excluded_antigravity += max(0, count)
            continue

        pick = _upgrader_pick_from_item(normalized_name, item)
        if pick is None:
            if _is_cell_furnace_item(item, normalized_name):
                excluded_cell_furnaces += max(0, count)
            elif _is_disabled_override_item(normalized_name):
                excluded_disabled += max(0, count)
            continue
        replicated = min(max(0, count), 30)
        for _ in range(replicated):
            candidates.append(pick)
    return (candidates, excluded_cell_furnaces, excluded_disabled, excluded_antigravity)


def _collect_furnace_candidates(
    items: dict[str, dict[str, Any]],
    counts: dict[str, int],
    allow_destroy_items: bool,
) -> list[UpgraderPick]:
    out: list[UpgraderPick] = []
    for normalized_name, count in counts.items():
        item = items.get(normalized_name)
        if not item:
            continue
        item_type = normalize_lookup(str(item.get("type") or ""))
        if item_type != "furnace":
            continue
        if _is_cell_furnace_item(item, normalized_name):
            continue
        if _is_disabled_override_item(normalized_name):
            continue

        pick = _upgrader_pick_from_item(normalized_name, item)
        if pick is None:
            continue
        if not allow_destroy_items and pick.destroys_ore:
            continue
        replicated = min(max(0, count), 10)
        for _ in range(replicated):
            out.append(pick)
    return out


def _mine_pick_from_item(normalized_name: str, item: dict[str, Any]) -> MinePick | None:
    profile = item.get("throughput_profile") or {}
    ore_rate = _as_float(profile.get("ores_per_second"))

    expected_vps: float | None = _as_float(profile.get("estimated_value_per_second"))
    base_ore_value: float | None = _as_float(profile.get("base_ore_value"))

    if expected_vps is None:
        min_vps = _as_float(profile.get("estimated_value_per_second_min"))
        max_vps = _as_float(profile.get("estimated_value_per_second_max"))
        if min_vps is not None and max_vps is not None:
            expected_vps = (min_vps + max_vps) / 2.0

    if base_ore_value is None:
        min_value = _as_float(profile.get("base_ore_value_min"))
        max_value = _as_float(profile.get("base_ore_value_max"))
        if min_value is not None and max_value is not None:
            base_ore_value = (min_value + max_value) / 2.0

    if expected_vps is None and ore_rate is not None and base_ore_value is not None:
        expected_vps = ore_rate * base_ore_value

    if expected_vps is None:
        return None

    ore_worth = item.get("ore_worth") or {}
    drop_rate = item.get("drop_rate") or {}
    confidence = _combined_confidence(
        _as_float(ore_worth.get("confidence"), 0.65),
        _as_float(drop_rate.get("confidence"), 0.65),
    )

    size = item.get("size") or {}
    width = int(size.get("width") or 1)
    length = int(size.get("length") or 1)
    tile_footprint = max(1, width * length)

    return MinePick(
        normalized_name=normalized_name,
        name=str(item.get("name") or normalized_name),
        ore_per_second=ore_rate,
        base_ore_value=base_ore_value,
        expected_value_per_second=max(0.0, expected_vps),
        confidence=confidence,
        tile_footprint=tile_footprint,
    )


def _infer_multiplier_value(item: dict[str, Any]) -> float | None:
    multiplier = item.get("multiplier") or {}
    direct = _as_float(multiplier.get("value"))
    if direct is not None and direct > 1:
        return direct

    candidates: list[float] = []
    effects = item.get("effects") or {}
    details = item.get("details") or {}
    multipliers = details.get("multipliers") if isinstance(details, dict) else None

    for source in [effects.get("x_values"), (multipliers or {}).get("x_values") if isinstance(multipliers, dict) else None]:
        if not isinstance(source, list):
            continue
        for token in source:
            parsed = _as_float(token)
            if parsed is not None and parsed > 1:
                candidates.append(parsed)

    extra = details.get("extra") if isinstance(details, dict) else None
    text_sources = [
        str((extra or {}).get("effects") or "") if isinstance(extra, dict) else "",
        str((extra or {}).get("drawbacks") or "") if isinstance(extra, dict) else "",
        str(item.get("description") or ""),
    ]
    for text in text_sources:
        text_norm = normalize_lookup(text)
        if not any(
            token in text_norm
            for token in ["multipl", "upgrade", "increas", "value by", "bonus"]
        ):
            continue
        for match in re.findall(r"([0-9]+(?:\.[0-9]+)?)\s*%", text):
            try:
                percent = float(match)
            except ValueError:
                continue
            if percent > 0:
                candidates.append(1.0 + (percent / 100.0))

    if not candidates:
        return None
    return min(candidates)


def _detect_portable_blaster(item: dict[str, Any], normalized_name: str) -> tuple[bool, bool]:
    details = item.get("details") or {}
    categories = details.get("categories") if isinstance(details, dict) else None
    category_norm = [normalize_lookup(str(entry)) for entry in categories] if isinstance(categories, list) else []

    is_portable = any("portable upgrader" in entry for entry in category_norm)
    is_blaster = any("blaster" in entry for entry in category_norm)

    name_text = normalize_lookup(str(item.get("name") or normalized_name))
    if "portable" in name_text:
        is_portable = True
    if "blaster" in name_text:
        is_blaster = True

    return (is_portable, is_blaster)


def _infer_upgrade_limit_max_uses(item: dict[str, Any], tags: dict[str, Any]) -> int | None:
    for raw in [
        tags.get("upgrade_limit"),
        (item.get("proof_and_limits") or {}).get("upgrade_limit"),
        ((item.get("details") or {}).get("extra") or {}).get("upgrade_limit"),
    ]:
        parsed = _parse_limit_value(raw)
        if parsed is not None:
            return parsed
    return None


def _parse_limit_value(raw: Any) -> int | None:
    if isinstance(raw, (int, float)):
        value = int(raw)
        return value if value > 0 else None

    if raw is None:
        return None

    text = normalize_lookup(str(raw))
    if not text:
        return None
    if any(token in text for token in ["none", "unlimited", "infinite", "no limit"]):
        return None

    numbers = [int(token) for token in re.findall(r"\d+", text)]
    if not numbers:
        return None
    best = max(numbers)
    return best if best > 0 else None


def _upgrader_pick_from_item(normalized_name: str, item: dict[str, Any]) -> UpgraderPick | None:
    if _is_disabled_override_item(normalized_name):
        return None

    if _is_antigravity_conveyor_item(item):
        return None

    item_type = normalize_lookup(str(item.get("type") or ""))
    if item_type == "furnace" and _is_cell_furnace_item(item, normalized_name):
        return None

    multiplier_value = _infer_multiplier_value(item)
    if multiplier_value is None or multiplier_value <= 1:
        return None

    effects = item.get("effects") or {}
    behaviors = set(effects.get("behaviors") or [])
    effect_tags = set((item.get("effect_tags") or {}).keys())
    tags = ((item.get("details") or {}).get("tags") or {})
    synergies = item.get("synergies") or {}
    synergy_keywords = set(synergies.get("keywords") or [])
    related_items = tuple(
        sorted(
            {
                normalize_lookup(str(entry.get("normalized_name") or entry.get("name") or ""))
                for entry in (synergies.get("related_items") or [])
                if isinstance(entry, dict)
            }
        )
    )
    effects_status = tuple(
        sorted(
            {
                normalize_lookup(str(token))
                for token in (effects.get("status_effects") or [])
                if token and normalize_lookup(str(token)) not in UNMODELED_STATUS_EFFECTS
            }
        )
    )
    unmodeled_status = tuple(
        sorted(
            {
                normalize_lookup(str(token))
                for token in (effects.get("status_effects") or [])
                if token and normalize_lookup(str(token)) in UNMODELED_STATUS_EFFECTS
            }
        )
    )

    mpu = item.get("mpu") or {}
    mpu_cap = _as_float(mpu.get("value"))

    throughput_profile = item.get("throughput_profile") or {}
    throughput_multiplier = _as_float(throughput_profile.get("throughput_multiplier"))
    if throughput_multiplier is None:
        min_mult = _as_float(throughput_profile.get("throughput_multiplier_min"))
        max_mult = _as_float(throughput_profile.get("throughput_multiplier_max"))
        if min_mult is not None and max_mult is not None:
            throughput_multiplier = (min_mult + max_mult) / 2.0

    conf = _as_float(effects.get("confidence"), 0.8)
    if "randomized" in behaviors:
        conf *= 0.8

    override = OVERRIDE_MULTIPLIER_TABLE.get(normalized_name)
    conditional_fire_multiplier: float | None = None
    overheated_multiplier: float | None = None
    override_applies_fire: bool | None = None
    override_extinguishes_fire: bool | None = None
    if override:
        override_base = _as_float(override.get("base_multiplier"))
        if override_base is not None and override_base > 1:
            multiplier_value = override_base
        conditional_fire_multiplier = _as_float(override.get("conditional_fire_multiplier"))
        overheated_multiplier = _as_float(override.get("overheated_multiplier"))
        if isinstance(override.get("force_applies_fire"), bool):
            override_applies_fire = bool(override.get("force_applies_fire"))
        if isinstance(override.get("force_extinguishes_fire"), bool):
            override_extinguishes_fire = bool(override.get("force_extinguishes_fire"))

    override_first_only: bool | None = None
    override_max_uses: int | None = None
    if override:
        if isinstance(override.get("first_only"), bool):
            override_first_only = bool(override.get("first_only"))
        max_uses = override.get("max_uses")
        if isinstance(max_uses, (int, float)):
            override_max_uses = max(1, int(max_uses))

    inferred_max_uses = _infer_upgrade_limit_max_uses(item, tags)
    resolved_max_uses = override_max_uses if override_max_uses is not None else inferred_max_uses

    is_portable, is_blaster = _detect_portable_blaster(item, normalized_name)

    applies_fire = ("sets_fire" in effect_tags) or ("fire" in set(effects.get("status_effects") or []))
    extinguish_text = normalize_lookup(str(item.get("description") or "") + " " + str(item.get("how_to_use") or ""))
    extinguishes_fire = any(
        token in extinguish_text
        for token in [
            "extinguishes",
            "extinguishes fire",
            "puts out fire",
            "removes fire",
        ]
    )
    if override_applies_fire is not None:
        applies_fire = override_applies_fire
    if override_extinguishes_fire is not None:
        extinguishes_fire = override_extinguishes_fire

    size = item.get("size") or {}
    width = int(size.get("width") or 1)
    length = int(size.get("length") or 1)
    tile_footprint = max(1, width * length)

    return UpgraderPick(
        normalized_name=normalized_name,
        name=str(item.get("name") or normalized_name),
        item_type=item_type,
        is_portable=is_portable,
        is_blaster=is_blaster,
        multiplier=multiplier_value,
        conditional_fire_multiplier=conditional_fire_multiplier,
        overheated_multiplier=overheated_multiplier,
        applies_fire=applies_fire,
        extinguishes_fire=extinguishes_fire,
        confidence=max(0.1, min(1.0, conf)),
        mpu_cap=mpu_cap,
        is_resetter="resetter" in behaviors,
        destroys_ore=("destroys_ore" in behaviors) or ("destroys_ore" in effect_tags),
        is_furnace=item_type == "furnace",
        is_cell_furnace=False,
        is_teleporter="teleporter" in behaviors,
        is_splitter="splitter" in behaviors,
        is_merger="merger" in behaviors,
        directional="directional" in behaviors,
        randomized="randomized" in behaviors,
        requires_condition="requires_condition" in synergy_keywords,
        excludes_condition="excludes_condition" in synergy_keywords,
        works_with="works_with" in synergy_keywords,
        status_effects=effects_status,
        unmodeled_effects=unmodeled_status,
        related_items=tuple(token for token in related_items if token),
        throughput_multiplier=throughput_multiplier,
        tile_footprint=tile_footprint,
        first_only=_is_first_only_upgrader(item, normalized_name, tags) if override_first_only is None else override_first_only,
        max_uses=resolved_max_uses,
    )


def _simulate_ordered_pipeline(
    upgraders: list[UpgraderPick],
    loop_cap: int,
) -> tuple[float, int, list[str], float, float | None]:
    if not upgraders:
        return (1.0, 1, ["No static upgrader multipliers found; using x1 baseline"], 1.0, None)

    notes: list[str] = []
    ordered = _order_chain_for_pipeline(upgraders)
    phases = _split_pipeline_phases(ordered)
    has_final_upgrader = any(item.normalized_name == "the final upgrader" for item in ordered)

    available_resetters = sum(
        1
        for item in ordered
        if item.is_resetter and item.normalized_name != "the final upgrader"
    )
    has_loop_topology = any(item.is_teleporter or item.is_splitter or item.is_merger for item in ordered)
    loop_cap = max(1, int(loop_cap))
    passes = 1
    if has_loop_topology and available_resetters >= 2:
        passes = min(loop_cap, 1 + available_resetters)
    resetter_efficiency = min(
        RESETTER_EFFICIENCY_MAX,
        RESETTER_BASE_EFFICIENCY + RESETTER_EFFICIENCY_STEP * max(0, available_resetters),
    )

    chain = 1.0
    active_statuses: set[str] = set()
    condition_hits = 0
    condition_misses = 0
    status_conflicts = 0
    overheated = False
    for pass_idx in range(passes):
        pass_diminish = resetter_efficiency**pass_idx
        pass_mult = 1.0
        for item in phases["pre"] + phases["core"] + phases["post"] + phases["output"]:
            effective_multiplier = max(1.0, item.multiplier)
            if item.normalized_name == "the final upgrader" and pass_idx > 0:
                effective_multiplier = 1.0
            if item.conditional_fire_multiplier is not None and "fire" in active_statuses:
                effective_multiplier = max(effective_multiplier, item.conditional_fire_multiplier)
            if item.overheated_multiplier is not None and overheated:
                effective_multiplier = min(effective_multiplier, item.overheated_multiplier)

            local_mult = 1.0 + (effective_multiplier - 1.0) * pass_diminish
            local_mult = 1.0 + (local_mult - 1.0) * item.confidence

            if item.randomized:
                local_mult = 1.0 + (local_mult - 1.0) * 0.85
            if item.requires_condition:
                if active_statuses:
                    local_mult = 1.0 + (local_mult - 1.0) * REQUIRES_CONDITION_HIT_MULT
                    condition_hits += 1
                else:
                    local_mult = 1.0 + (local_mult - 1.0) * REQUIRES_CONDITION_MISS_MULT
                    condition_misses += 1
            if item.excludes_condition:
                if active_statuses:
                    local_mult = 1.0 + (local_mult - 1.0) * EXCLUDES_CONDITION_HIT_MULT
                    condition_misses += 1
                else:
                    local_mult = 1.0 + (local_mult - 1.0) * EXCLUDES_CONDITION_MISS_MULT
                    condition_hits += 1
            if item.directional:
                local_mult = 1.0 + (local_mult - 1.0) * 0.92
            if item.works_with:
                local_mult *= 1.02

            next_statuses = set(active_statuses)
            if item.applies_fire:
                next_statuses.add("fire")
            if item.extinguishes_fire:
                next_statuses.discard("fire")

            for status in item.status_effects:
                if status:
                    next_statuses.add(status)

            if "fire" in next_statuses and ("wet" in next_statuses or "frost" in next_statuses):
                local_mult = 1.0 + (local_mult - 1.0) * STATUS_CONFLICT_MULT
                status_conflicts += 1

            pass_mult *= local_mult
            if pass_mult > 1e9:
                pass_mult = 1e9
                break

            active_statuses = next_statuses

            if item.normalized_name == "cooling chamber":
                if pass_idx >= 1:
                    overheated = True

        chain *= pass_mult
        if chain > 1e18:
            chain = 1e18
            break

    uncapped_chain = max(1.0, chain)
    mpu_caps = [item.mpu_cap for item in ordered if item.mpu_cap is not None and item.mpu_cap > 1]
    applied_cap: float | None = None
    if mpu_caps:
        notes.append(
            f"MPU data detected (tightest x{min(mpu_caps):g}); treated as informational (no hard chain clamp)"
        )

    notes.append(
        f"Ordered pipeline simulation: pre={len(phases['pre'])}, core={len(phases['core'])}, "
        f"post={len(phases['post'])}, output={len(phases['output'])}; passes={passes}"
    )
    notes.append(
        f"Loop realism: resetters={available_resetters}, efficiency={resetter_efficiency:.2f}, pass_decay={pass_diminish:.2f}"
    )
    if not has_loop_topology and available_resetters > 0:
        notes.append("Resetters detected but no loop topology pieces; using single-pass simulation")
    if condition_hits or condition_misses:
        notes.append(f"Condition context: hits={condition_hits}, misses={condition_misses}")
    if status_conflicts > 0:
        notes.append(f"Status conflict damping applied {status_conflicts} time(s)")
    overridden = [item.name for item in ordered if item.normalized_name in OVERRIDE_MULTIPLIER_TABLE]
    if overridden:
        notes.append(
            "Applied manual multiplier overrides "
            f"({OVERRIDE_RULESET_VERSION}) for: "
            + ", ".join(sorted(set(overridden)))
        )
    if has_final_upgrader and passes > 1:
        notes.append("The Final Upgrader modeled as first-pass only; second/third pass effects skipped")
    return (max(1.0, chain), passes, notes, uncapped_chain, applied_cap)


def _estimate_ordered_bottleneck(upgraders: list[UpgraderPick]) -> tuple[float, list[str]]:
    if not upgraders:
        return (1.0, ["No conveyor bottleneck data available"])

    ordered = _order_chain_for_pipeline(upgraders)
    phases = _split_pipeline_phases(ordered)
    notes: list[str] = []

    def phase_floor(items: list[UpgraderPick]) -> float:
        vals = [item.throughput_multiplier for item in items if item.throughput_multiplier is not None]
        if not vals:
            return 1.0
        return min(vals)

    pre_floor = phase_floor(phases["pre"])
    core_floor = phase_floor(phases["core"])
    post_floor = phase_floor(phases["post"])
    out_floor = phase_floor(phases["output"])

    teleporter_tax = 1.0 - min(0.18, 0.04 * sum(1 for item in ordered if item.is_teleporter))
    splitter_tax = 1.0 - min(0.2, 0.06 * sum(1 for item in ordered if item.is_splitter or item.is_merger))

    chain_flow = 1.0
    slow_segments: list[tuple[str, float]] = []
    speedup_segments = 0
    for item in ordered:
        speed = item.throughput_multiplier
        if speed is None:
            continue
        link = max(0.05, min(4.0, speed))
        if link < 1.0:
            chain_flow *= link
            slow_segments.append((item.name, link))
        elif link > 1.0:
            chain_flow *= 1.0 + min(0.18, (link - 1.0) * 0.12)
            speedup_segments += 1

    bottleneck = max(0.03, min(5.0, chain_flow * teleporter_tax * splitter_tax))

    notes.append(
        f"Ordered throughput floors pre/core/post/output = {pre_floor:.2f}/{core_floor:.2f}/{post_floor:.2f}/{out_floor:.2f}"
    )
    notes.append(f"Cumulative conveyor flow estimate across chain: x{chain_flow:.3f}")
    if slow_segments:
        preview = ", ".join(f"{name} x{value:.2f}" for name, value in slow_segments[:3])
        suffix = " ..." if len(slow_segments) > 3 else ""
        notes.append(f"Conveyor-limited segments: {preview}{suffix}")
    if speedup_segments > 0:
        notes.append(f"Conveyor speed boosts detected on {speedup_segments} segment(s)")
    if teleporter_tax < 1.0 or splitter_tax < 1.0:
        notes.append(f"Topology throughput tax applied: teleporter={teleporter_tax:.2f}, split_merge={splitter_tax:.2f}")

    return (bottleneck, notes)


def _order_chain_for_pipeline(upgraders: list[UpgraderPick]) -> list[UpgraderPick]:
    # Keep Big Bertha at chain entry when present (must be first-only in realistic setups).
    def stage_rank(item: UpgraderPick) -> int:
        if item.normalized_name == "big bertha":
            return -2
        if item.first_only:
            return -1
        if item.is_splitter:
            return 0
        if item.is_teleporter:
            return 1
        if item.is_merger:
            return 2
        if item.is_furnace:
            return 4
        return 3

    return sorted(upgraders, key=lambda item: (stage_rank(item), -_upgrader_priority(item)))


def _split_pipeline_phases(upgraders: list[UpgraderPick]) -> dict[str, list[UpgraderPick]]:
    pre: list[UpgraderPick] = []
    core: list[UpgraderPick] = []
    post: list[UpgraderPick] = []
    output: list[UpgraderPick] = []

    for item in upgraders:
        if item.is_splitter or item.is_teleporter:
            pre.append(item)
        elif item.is_merger:
            post.append(item)
        elif item.is_furnace:
            output.append(item)
        else:
            core.append(item)

    return {
        "pre": pre,
        "core": core,
        "post": post,
        "output": output,
    }


def _dimension_penalty(tile_ratio: float) -> float:
    if tile_ratio <= 0.7:
        return 1.0
    if tile_ratio <= 1.0:
        pressure = (tile_ratio - 0.7) / 0.3
        return max(0.72, 1.0 - 0.28 * pressure)
    overflow = min(1.5, tile_ratio - 1.0)
    return max(0.35, 0.72 - 0.25 * overflow)


def _build_limiter_recommendation(
    mpu_factor: float,
    conveyor_factor: float,
    dimension_factor: float,
    destruction_factor: float,
) -> str:
    factors = {
        "mpu": max(0.0, min(1.0, mpu_factor)),
        "conveyor": max(0.0, min(1.0, conveyor_factor)),
        "dimensions": max(0.0, min(1.0, dimension_factor)),
        "stability": max(0.0, min(1.0, destruction_factor)),
    }
    limiter = min(factors, key=factors.get)
    severity = factors[limiter]

    if severity >= 0.98:
        return "No dominant limiter; tune MPU and conveyor together"
    if limiter == "mpu":
        return "Prioritize higher MPU-effective chain pieces; MPU is the tightest limiter"
    if limiter == "conveyor":
        return "Prioritize conveyor throughput/speed pieces; flow bottleneck is strongest"
    if limiter == "dimensions":
        return "Reduce footprint or improve compactness; base size pressure is limiting"
    return "Reduce ore-destroy risk or add safer output path; stability loss is limiting"


def _evaluate_chain_synergy(upgraders: list[UpgraderPick]) -> tuple[float, list[str]]:
    if not upgraders:
        return (1.0, [])

    notes: list[str] = []
    by_name = {item.normalized_name for item in upgraders}

    synergy_hits = 0
    related_hits = 0
    condition_penalties = 0

    status_counts: dict[str, int] = {}
    for item in upgraders:
        if item.works_with:
            synergy_hits += 1

        if item.related_items:
            if any(token in by_name for token in item.related_items):
                related_hits += 1

        if item.requires_condition or item.excludes_condition:
            condition_penalties += 1

        for status in item.status_effects:
            status_counts[status] = status_counts.get(status, 0) + 1

    status_bonus = 0.0
    status_conflicts = 0
    for status, count in status_counts.items():
        if count >= 2:
            status_bonus += min(0.03 * (count - 1), 0.09)
        if status in {"fire", "frost", "wet"} and count >= 3:
            status_conflicts += 1

    bonus = min(0.22, 0.02 * synergy_hits + 0.03 * related_hits + status_bonus)
    penalty = min(0.25, 0.025 * condition_penalties + 0.04 * status_conflicts)
    multiplier = max(0.75, 1.0 + bonus - penalty)

    if synergy_hits or related_hits:
        notes.append(
            f"Synergy boost: works_with={synergy_hits}, related_pair_hits={related_hits}, status_bonus={status_bonus:.2f}"
        )
    if condition_penalties or status_conflicts:
        notes.append(
            f"Synergy penalties: conditional={condition_penalties}, status_conflicts={status_conflicts}"
        )

    notes.append(f"Synergy multiplier applied: x{multiplier:.3f}")
    return (multiplier, notes)


def _estimate_single_ore_base_value(
    selected_mines: list[MinePick],
    mine_vps: float,
    base_ores_per_second: float,
) -> float | None:
    weighted_total = 0.0
    weighted_rate = 0.0
    fallback_values: list[float] = []

    for mine in selected_mines:
        if mine.base_ore_value is not None and mine.base_ore_value > 0:
            fallback_values.append(mine.base_ore_value)
            if mine.ore_per_second is not None and mine.ore_per_second > 0:
                weighted_total += mine.base_ore_value * mine.ore_per_second
                weighted_rate += mine.ore_per_second

    if weighted_rate > 0:
        return max(0.0, weighted_total / weighted_rate)

    if fallback_values:
        return max(0.0, sum(fallback_values) / len(fallback_values))

    if base_ores_per_second > 0:
        return max(0.0, mine_vps / base_ores_per_second)
    return None


def _minimum_items_for_single_ore_target(
    base_single_ore_value: float,
    target_value: float,
    ordered_upgraders: list[UpgraderPick],
    loop_cap: int,
) -> tuple[int | None, str | None]:
    if base_single_ore_value <= 0 or target_value <= 0:
        return (None, None)

    if base_single_ore_value >= target_value:
        return (0, None)

    for idx in range(1, len(ordered_upgraders) + 1):
        subset = ordered_upgraders[:idx]
        multiplier, _passes, _notes, _uncapped, _mpu_cap = _simulate_ordered_pipeline(subset, loop_cap)
        synergy_multiplier, _synergy_notes = _evaluate_chain_synergy(subset)
        destruction_penalty = _destruction_penalty(subset)
        final_single_ore = base_single_ore_value * multiplier * synergy_multiplier * destruction_penalty
        if final_single_ore >= target_value:
            return (idx, subset[-1].name)

    return (None, None)


def _is_cell_furnace_item(item: dict[str, Any], normalized_name: str) -> bool:
    furnace_input = item.get("furnace_input") or {}
    if isinstance(furnace_input, dict):
        if bool(furnace_input.get("is_cell_furnace")):
            return True
        if str(furnace_input.get("policy") or "") == "raw_only":
            return True

    details = item.get("details") or {}
    details_furnace = details.get("furnace_input") if isinstance(details, dict) else None
    if isinstance(details_furnace, dict):
        if bool(details_furnace.get("is_cell_furnace")):
            return True
        if str(details_furnace.get("policy") or "") == "raw_only":
            return True

    name_text = normalize_lookup(str(item.get("name") or normalized_name))
    description = normalize_lookup(str(item.get("description") or ""))
    how_to_use = normalize_lookup(str(item.get("how_to_use") or ""))
    combined = " ".join([name_text, description, how_to_use])
    if "cell furnace" not in combined:
        return False
    if "only accepts ores directly from droppers" in combined:
        return True
    if "raw ore" in combined and "upgraded" in combined:
        return True
    if "cannot" in combined and "upgraded" in combined:
        return True
    return "cell furnace" in name_text


def _is_antigravity_conveyor_item(item: dict[str, Any]) -> bool:
    details = item.get("details") or {}
    tags = details.get("tags") if isinstance(details, dict) else None
    extra = details.get("extra") if isinstance(details, dict) else None
    categories = details.get("categories") if isinstance(details, dict) else None

    elevations: list[str] = []
    for value in [
        (tags or {}).get("conveyor_elevation") if isinstance(tags, dict) else None,
        (extra or {}).get("conveyor_elevation") if isinstance(extra, dict) else None,
    ]:
        if isinstance(value, str) and value.strip():
            elevations.append(normalize_lookup(value))

    if not elevations:
        category_norm = [normalize_lookup(str(entry)) for entry in categories] if isinstance(categories, list) else []
        return any("anti gravity" in token for token in category_norm)

    return any("anti gravity" in elevation for elevation in elevations)


def _is_disabled_override_item(normalized_name: str) -> bool:
    override = OVERRIDE_MULTIPLIER_TABLE.get(normalized_name)
    return bool(override and isinstance(override.get("disabled"), bool) and override.get("disabled"))


def _is_first_only_upgrader(item: dict[str, Any], normalized_name: str, tags: dict[str, Any]) -> bool:
    name = normalize_lookup(str(item.get("name") or normalized_name))
    if name == "big bertha":
        return True

    texts = [
        str(item.get("description") or ""),
        str(item.get("how_to_use") or ""),
        str(((item.get("details") or {}).get("extra") or {}).get("drawbacks") or ""),
        str(((item.get("details") or {}).get("extra") or {}).get("effects") or ""),
        str(tags.get("upgrade_counter") or ""),
    ]
    joined = normalize_lookup(" ".join(texts))
    signals = [
        "requires ore to be launched",
        "directly dropped into upgrader part",
        "first upgrader",
        "must be first",
        "raw ore only",
    ]
    return any(signal in joined for signal in signals)


def _destruction_penalty(upgraders: list[UpgraderPick]) -> float:
    risky = sum(1 for item in upgraders if item.destroys_ore)
    if risky <= 0:
        return 1.0
    furnace_relief = sum(1 for item in upgraders if item.is_furnace)
    base = max(0.35, 0.92**risky)
    relief = min(1.0, 0.03 * furnace_relief)
    return min(1.0, base + relief)


def _upgrader_priority(item: UpgraderPick) -> float:
    base = math.log(max(1.0, item.multiplier), 1.2) * item.confidence
    reset_bonus = 0.8 if item.is_resetter else 0.0
    destroy_penalty = 0.7 if item.destroys_ore else 0.0
    throughput_bonus = 0.0
    if item.throughput_multiplier is not None and item.throughput_multiplier > 1:
        throughput_bonus = min(0.5, (item.throughput_multiplier - 1.0) * 0.08)

    topology_bonus = 0.0
    if item.is_teleporter:
        topology_bonus += 0.18
    if item.is_splitter or item.is_merger:
        topology_bonus += 0.15
    if item.is_furnace:
        topology_bonus += 0.22
    condition_penalty = 0.0
    if item.requires_condition:
        condition_penalty += 0.2
    if item.excludes_condition:
        condition_penalty += 0.1
    if item.directional:
        condition_penalty += 0.08
    if item.randomized:
        condition_penalty += 0.15

    if item.conditional_fire_multiplier is not None and item.conditional_fire_multiplier > item.multiplier:
        base += min(0.4, math.log(item.conditional_fire_multiplier / max(1.0, item.multiplier), 1.6) * 0.2)

    return base + reset_bonus + throughput_bonus + topology_bonus - destroy_penalty - condition_penalty


def _combined_confidence(a: float | None, b: float | None) -> float:
    left = 0.7 if a is None else a
    right = 0.7 if b is None else b
    return max(0.05, min(1.0, left * right))


def _select_legal_upgrader_chain(
    candidates: list[UpgraderPick],
    furnace_candidates: list[UpgraderPick],
    max_items: int,
    loop_cap: int,
    allow_destroy_items: bool,
) -> tuple[list[UpgraderPick], UpgraderPick | None, list[str]]:
    if not candidates:
        return ([], None, ["No upgrader/furnace candidates available"])

    ordered = sorted(candidates, key=_upgrader_priority, reverse=True)
    selected: list[UpgraderPick] = []
    notes: list[str] = []
    selected_furnace: UpgraderPick | None = None

    if furnace_candidates:
        ordered_furnaces = sorted(furnace_candidates, key=_upgrader_priority, reverse=True)
        selected_furnace = ordered_furnaces[0]
        notes.append(f"Selected furnace: {selected_furnace.name}")

    resetters = 0
    teleporters = 0
    splitters = 0
    mergers = 0

    max_items = max(1, int(max_items))
    loop_cap = max(1, int(loop_cap))
    max_resetters = max(1, loop_cap - 1)
    max_teleporters = 4
    max_splitters = 3
    max_mergers = 3

    destroy_filtered_out = 0
    first_only_selected = False
    use_counts: dict[str, int] = {}

    def can_add(candidate: UpgraderPick) -> bool:
        if candidate.is_resetter and resetters >= max_resetters:
            return False
        if candidate.is_teleporter and teleporters >= max_teleporters:
            return False
        if candidate.is_splitter and splitters >= max_splitters:
            return False
        if candidate.is_merger and mergers >= max_mergers:
            return False
        if candidate.is_furnace:
            return False

        if candidate.first_only and first_only_selected:
            return False

        if candidate.max_uses is not None:
            used = use_counts.get(candidate.normalized_name, 0)
            if used >= candidate.max_uses:
                return False

        if not allow_destroy_items and candidate.destroys_ore:
            return False

        if candidate.destroys_ore:
            current_destroys = sum(1 for item in selected if item.destroys_ore)
            if current_destroys >= 5:
                return False

        if candidate.requires_condition:
            if sum(1 for item in selected if item.requires_condition) >= 8:
                return False

        return True

    for candidate in ordered:
        if len(selected) >= max_items:
            break

        if not allow_destroy_items and candidate.destroys_ore:
            destroy_filtered_out += 1
            continue

        if not can_add(candidate):
            continue

        selected.append(candidate)
        use_counts[candidate.normalized_name] = use_counts.get(candidate.normalized_name, 0) + 1
        if candidate.first_only:
            first_only_selected = True
        if candidate.is_resetter:
            resetters += 1
        if candidate.is_teleporter:
            teleporters += 1
        if candidate.is_splitter:
            splitters += 1
        if candidate.is_merger:
            mergers += 1
    if not selected:
        fallback = ordered[: min(max_items, 4)]
        selected = [replace(item) for item in fallback]
        notes.append("Fell back to top-ranked chain due to strict legality filters")

    resetters = sum(1 for item in selected if item.is_resetter)
    teleporters = sum(1 for item in selected if item.is_teleporter)
    splitters = sum(1 for item in selected if item.is_splitter)
    mergers = sum(1 for item in selected if item.is_merger)
    first_only_selected = any(item.first_only for item in selected)
    use_counts = {}
    for item in selected:
        use_counts[item.normalized_name] = use_counts.get(item.normalized_name, 0) + 1

    if not any(item.is_portable or item.is_blaster for item in selected):
        portable_or_blaster_pool = [candidate for candidate in ordered if candidate.is_portable or candidate.is_blaster]
        forced = next(
            (
                candidate
                for candidate in portable_or_blaster_pool
                if (candidate.is_portable or candidate.is_blaster)
                and candidate.normalized_name not in {item.normalized_name for item in selected}
                and can_add(candidate)
            ),
            None,
        )
        if forced is not None:
            selected.append(forced)
            use_counts[forced.normalized_name] = use_counts.get(forced.normalized_name, 0) + 1
            if forced.first_only:
                first_only_selected = True
            if forced.is_resetter:
                resetters += 1
            if forced.is_teleporter:
                teleporters += 1
            if forced.is_splitter:
                splitters += 1
            if forced.is_merger:
                mergers += 1
            notes.append(
                f"Forced inclusion: {forced.name} (portable/blaster) added outside total item count cap"
            )
        elif portable_or_blaster_pool:
            notes.append("Portable/blaster candidates found, but none passed current legality filters")

    if selected_furnace is not None:
        selected.append(selected_furnace)

    if any(item.first_only for item in selected):
        notes.append("Applied first-only upgrader rules (e.g. Big Bertha must be chain entry and single-use)")

    notes.append(
        "Chain legality filters: "
        f"resetters<={max_resetters}, teleporters<={max_teleporters}, splitters<={max_splitters}, mergers<={max_mergers}"
    )
    if not allow_destroy_items:
        notes.append(f"No-destroy mode enabled; filtered {destroy_filtered_out} destroy item candidate(s)")
    return (selected, selected_furnace, notes)


def _select_target_upgrader_chain(
    candidates: list[UpgraderPick],
    furnace_candidates: list[UpgraderPick],
    max_items: int,
    loop_cap: int,
    allow_destroy_items: bool,
    mine_vps: float,
    base_ores_per_second: float,
    base_single_ore_value: float | None,
    target_value: float,
    tile_limit: int,
    mine_tile_footprint: int,
) -> tuple[list[UpgraderPick], UpgraderPick | None, list[str]]:
    if not candidates:
        return ([], None, ["Target beam search skipped: no upgrader/furnace candidates available"])

    loop_cap = max(1, int(loop_cap))
    max_items = max(1, int(max_items))
    max_resetters = max(1, loop_cap - 1)
    max_teleporters = 4
    max_splitters = 3
    max_mergers = 3

    grouped_candidates: dict[str, tuple[UpgraderPick, int]] = {}
    for candidate in candidates:
        existing = grouped_candidates.get(candidate.normalized_name)
        if existing is None:
            grouped_candidates[candidate.normalized_name] = (candidate, 1)
        else:
            grouped_candidates[candidate.normalized_name] = (existing[0], existing[1] + 1)

    deduped_candidates = [
        _TargetCandidate(pick=pair[0], max_available=pair[1]) for pair in grouped_candidates.values()
    ]
    deduped_candidates.sort(key=lambda item: _upgrader_priority(item.pick), reverse=True)
    if len(deduped_candidates) > TARGET_BEAM_POOL_LIMIT:
        deduped_candidates = deduped_candidates[:TARGET_BEAM_POOL_LIMIT]

    selected_furnace: UpgraderPick | None = None
    if furnace_candidates:
        selected_furnace = sorted(furnace_candidates, key=_upgrader_priority, reverse=True)[0]

    pool_size = len(deduped_candidates)
    start_eval = _evaluate_target_chain(
        chain=[],
        selected_furnace=selected_furnace,
        mine_vps=mine_vps,
        base_ores_per_second=base_ores_per_second,
        base_single_ore_value=base_single_ore_value,
        target_value=target_value,
        loop_cap=loop_cap,
        tile_limit=tile_limit,
        mine_tile_footprint=mine_tile_footprint,
    )
    start = _TargetBeamState(
        use_counts=tuple(0 for _ in range(pool_size)),
        chain=tuple(),
        item_count=0,
        resetters=0,
        teleporters=0,
        splitters=0,
        mergers=0,
        destroy_count=0,
        requires_condition_count=0,
        first_only_selected=False,
        final_upgrader_uses=0,
        eval_result=start_eval,
    )

    def reaches_target(eval_result: _TargetEval) -> bool:
        return eval_result.single_ore_value >= target_value or (
            eval_result.target_seconds is not None and eval_result.target_seconds <= MAX_TARGET_ETA_SECONDS
        )

    beam: list[_TargetBeamState] = [start]
    best_state: _TargetBeamState = start
    best_reached_target = reaches_target(start.eval_result)
    evaluations = 1
    retained_states = 1
    budget_hit = False

    for _depth in range(1, max_items + 1):
        layer_states: list[_TargetBeamState] = []
        for state in beam:
            expansion = 0
            for idx, wrapped in enumerate(deduped_candidates):
                if expansion >= TARGET_BEAM_BRANCH:
                    break
                candidate = wrapped.pick
                if not _target_can_add_candidate(
                    state=state,
                    candidate=candidate,
                    use_count=state.use_counts[idx],
                    max_available=wrapped.max_available,
                    allow_destroy_items=allow_destroy_items,
                    max_resetters=max_resetters,
                    max_teleporters=max_teleporters,
                    max_splitters=max_splitters,
                    max_mergers=max_mergers,
                ):
                    continue

                use_counts = list(state.use_counts)
                use_counts[idx] += 1
                next_chain = state.chain + (candidate,)
                next_eval = _evaluate_target_chain(
                    chain=list(next_chain),
                    selected_furnace=selected_furnace,
                    mine_vps=mine_vps,
                    base_ores_per_second=base_ores_per_second,
                    base_single_ore_value=base_single_ore_value,
                    target_value=target_value,
                    loop_cap=loop_cap,
                    tile_limit=tile_limit,
                    mine_tile_footprint=mine_tile_footprint,
                )
                evaluations += 1
                next_state = _TargetBeamState(
                    use_counts=tuple(use_counts),
                    chain=next_chain,
                    item_count=state.item_count + 1,
                    resetters=state.resetters + (1 if candidate.is_resetter else 0),
                    teleporters=state.teleporters + (1 if candidate.is_teleporter else 0),
                    splitters=state.splitters + (1 if candidate.is_splitter else 0),
                    mergers=state.mergers + (1 if candidate.is_merger else 0),
                    destroy_count=state.destroy_count + (1 if candidate.destroys_ore else 0),
                    requires_condition_count=state.requires_condition_count + (1 if candidate.requires_condition else 0),
                    first_only_selected=state.first_only_selected or candidate.first_only,
                    final_upgrader_uses=state.final_upgrader_uses + (1 if candidate.normalized_name == "the final upgrader" else 0),
                    eval_result=next_eval,
                )

                next_reached_target = reaches_target(next_eval)
                if _is_better_target_eval(next_eval, best_state.eval_result, next_reached_target, best_reached_target):
                    best_state = next_state
                    best_reached_target = next_reached_target

                layer_states.append(next_state)
                expansion += 1

                if evaluations >= TARGET_BEAM_MAX_EVALUATIONS:
                    budget_hit = True
                    break
            if budget_hit:
                break
        if not layer_states:
            break

        best_by_signature: dict[tuple[int, ...], _TargetBeamState] = {}
        for state in layer_states:
            prior = best_by_signature.get(state.use_counts)
            if prior is None:
                best_by_signature[state.use_counts] = state
                continue
            state_reached = reaches_target(state.eval_result)
            prior_reached = reaches_target(prior.eval_result)
            if _is_better_target_eval(state.eval_result, prior.eval_result, state_reached, prior_reached):
                best_by_signature[state.use_counts] = state

        ranked = sorted(best_by_signature.values(), key=lambda state: (state.eval_result.score, state.item_count))
        beam = ranked[:TARGET_BEAM_WIDTH]
        retained_states += len(beam)

        reached_on_layer = [state for state in beam if reaches_target(state.eval_result)]
        if reached_on_layer:
            best_on_layer = min(
                reached_on_layer,
                key=lambda state: (
                    state.eval_result.target_seconds
                    if state.eval_result.target_seconds is not None
                    else float("inf"),
                    state.item_count,
                    state.eval_result.score,
                ),
            )
            if _is_better_target_eval(best_on_layer.eval_result, best_state.eval_result, True, best_reached_target):
                best_state = best_on_layer
            best_reached_target = True
            break

        if budget_hit:
            break

    if best_state.item_count <= 0:
        selected, selected_furnace, fallback_notes = _select_legal_upgrader_chain(
            candidates,
            furnace_candidates=furnace_candidates,
            max_items=max_items,
            loop_cap=loop_cap,
            allow_destroy_items=allow_destroy_items,
        )
        fallback_notes.append("Target beam fallback: used legacy greedy chain")
        return (selected, selected_furnace, fallback_notes)

    notes = [
        f"Target beam search evaluated {evaluations} chain(s)",
        f"Target beam retained {retained_states} state(s)",
        f"Target beam config: width={TARGET_BEAM_WIDTH}, branch={TARGET_BEAM_BRANCH}, pool={pool_size}",
        "Target objective: minimize ETA with item-count tie-break",
    ]
    if budget_hit:
        notes.append("Target beam hit evaluation budget; returning best result found")
    if selected_furnace is not None:
        notes.append(f"Selected furnace: {selected_furnace.name}")

    if not best_reached_target:
        notes.append("Target beam did not fully satisfy target within cap; returning best available chain")

    notes.append(
        "Target legality filters: "
        f"resetters<={max_resetters}, teleporters<={max_teleporters}, splitters<={max_splitters}, mergers<={max_mergers}"
    )
    if not allow_destroy_items:
        notes.append("No-destroy mode enabled for target beam search")

    final_chain = list(best_state.chain)
    if selected_furnace is not None:
        final_chain.append(selected_furnace)
    return (final_chain, selected_furnace, notes)


def _target_can_add_candidate(
    state: _TargetBeamState,
    candidate: UpgraderPick,
    use_count: int,
    max_available: int,
    allow_destroy_items: bool,
    max_resetters: int,
    max_teleporters: int,
    max_splitters: int,
    max_mergers: int,
) -> bool:
    if use_count >= max_available:
        return False
    if candidate.is_furnace:
        return False
    if candidate.first_only and state.first_only_selected:
        return False
    if candidate.max_uses is not None and use_count >= candidate.max_uses:
        return False
    if not allow_destroy_items and candidate.destroys_ore:
        return False
    if candidate.is_resetter and state.resetters >= max_resetters:
        return False
    if candidate.is_teleporter and state.teleporters >= max_teleporters:
        return False
    if candidate.is_splitter and state.splitters >= max_splitters:
        return False
    if candidate.is_merger and state.mergers >= max_mergers:
        return False
    if candidate.destroys_ore and state.destroy_count >= 5:
        return False
    if candidate.requires_condition and state.requires_condition_count >= 8:
        return False

    if candidate.normalized_name == "the final upgrader" and state.final_upgrader_uses >= 1:
        return False
    return True


def _evaluate_target_chain(
    chain: list[UpgraderPick],
    selected_furnace: UpgraderPick | None,
    mine_vps: float,
    base_ores_per_second: float,
    base_single_ore_value: float | None,
    target_value: float,
    loop_cap: int,
    tile_limit: int,
    mine_tile_footprint: int,
) -> _TargetEval:
    full_chain = list(chain)
    if selected_furnace is not None:
        full_chain.append(selected_furnace)

    bottleneck_multiplier, _notes = _estimate_ordered_bottleneck(full_chain)
    effective_ores_per_second = base_ores_per_second
    if base_ores_per_second > 0:
        effective_ores_per_second = base_ores_per_second * min(1.0, bottleneck_multiplier)
    throughput_ratio = 1.0 if base_ores_per_second <= 0 else max(0.02, effective_ores_per_second / base_ores_per_second)

    estimated_multiplier, _loop_passes, _stage_notes, _uncapped_multiplier, _mpu_cap = _simulate_ordered_pipeline(
        full_chain,
        loop_cap,
    )
    synergy_multiplier, _synergy_notes = _evaluate_chain_synergy(full_chain)
    combined_multiplier = estimated_multiplier * synergy_multiplier

    destruction_penalty = _destruction_penalty(full_chain)
    used_tiles = mine_tile_footprint + sum(item.tile_footprint for item in full_chain)
    tile_ratio = used_tiles / max(1, int(tile_limit))
    dimension_penalty = _dimension_penalty(tile_ratio)

    estimated_total_vps = mine_vps * throughput_ratio * combined_multiplier * destruction_penalty * dimension_penalty
    target_seconds = None if estimated_total_vps <= 0 else target_value / estimated_total_vps

    single_ore_value = 0.0
    if base_single_ore_value is not None and base_single_ore_value > 0:
        single_ore_value = base_single_ore_value * combined_multiplier * destruction_penalty * dimension_penalty

    eta_for_score = target_seconds if target_seconds is not None else MAX_TARGET_ETA_SECONDS * 12.0
    overflow_penalty = 0.0
    if eta_for_score > MAX_TARGET_ETA_SECONDS:
        overflow_penalty = (eta_for_score - MAX_TARGET_ETA_SECONDS) * 1.1

    score = eta_for_score + overflow_penalty + (len(chain) * TARGET_SEARCH_ITEM_PENALTY_SECONDS)

    return _TargetEval(
        estimated_total_vps=max(0.0, estimated_total_vps),
        target_seconds=target_seconds,
        single_ore_value=max(0.0, single_ore_value),
        score=score,
        item_count=len(chain),
    )


def _is_better_target_eval(
    candidate: _TargetEval,
    best: _TargetEval | None,
    candidate_reached: bool,
    best_reached: bool,
) -> bool:
    if best is None:
        return True
    if candidate_reached and not best_reached:
        return True
    if best_reached and not candidate_reached:
        return False

    cand_eta = candidate.target_seconds if candidate.target_seconds is not None else float("inf")
    best_eta = best.target_seconds if best.target_seconds is not None else float("inf")
    if cand_eta < best_eta - 1e-9:
        return True
    if best_eta < cand_eta - 1e-9:
        return False
    if candidate.item_count < best.item_count:
        return True
    if candidate.item_count > best.item_count:
        return False
    return candidate.score < best.score


def _build_progression_opportunities(
    items: dict[str, dict[str, Any]],
    counts: dict[str, int],
) -> list[ProgressionOpportunity]:
    opportunities: list[ProgressionOpportunity] = []
    if not items:
        return opportunities

    owned_names = {name for name, qty in counts.items() if int(qty) > 0}

    for normalized_name, item in items.items():
        if not isinstance(item, dict):
            continue
        if normalized_name in owned_names:
            continue

        target_tier = str(item.get("tier") or "")
        tier_norm = normalize_lookup(target_tier)
        if not any(token in tier_norm for token in ["fusion", "evolved", "evolution", "advanced fusion", "advanced evolution"]):
            continue

        ingredient_names = _extract_progression_ingredients(item, items)
        if not ingredient_names:
            continue

        required_total = len(ingredient_names)
        owned_total = sum(1 for token in ingredient_names if token in owned_names)
        missing = tuple(token for token in ingredient_names if token not in owned_names)
        closeness = 0.0 if required_total <= 0 else min(1.0, owned_total / required_total)
        ready_now = owned_total >= required_total and required_total > 0

        opportunities.append(
            ProgressionOpportunity(
                target_name=str(item.get("name") or normalized_name),
                target_tier=target_tier or "Unknown",
                target_type=str(item.get("type") or "Unknown"),
                required_total=required_total,
                owned_total=owned_total,
                closeness=closeness,
                ready_now=ready_now,
                ingredient_names=ingredient_names,
                missing_names=missing,
            )
        )

    opportunities.sort(
        key=lambda entry: (
            0 if entry.ready_now else 1,
            -entry.closeness,
            len(entry.missing_names),
            entry.target_name.lower(),
        )
    )
    return opportunities


def _extract_progression_ingredients(
    item: dict[str, Any],
    items: dict[str, dict[str, Any]],
) -> tuple[str, ...]:
    target_name = normalize_lookup(str(item.get("name") or ""))
    name_index: dict[str, str] = {}
    for normalized_name, payload in items.items():
        if not isinstance(payload, dict):
            continue
        display_name = str(payload.get("name") or normalized_name)
        name_index[normalize_lookup(display_name)] = normalized_name
        name_index[normalized_name] = normalized_name
        aliases = payload.get("aliases") or []
        if isinstance(aliases, list):
            for alias in aliases:
                alias_norm = normalize_lookup(str(alias))
                if alias_norm:
                    name_index[alias_norm] = normalized_name

    typo_aliases = {
        "flamingchrodinger": "flaming schrodinger",
        "dreamer nightmare": "dreamers nightmare",
        "dreamer terror": "dreamers terror",
        "dreamer valor": "dreamers valor",
    }
    for typo, canonical in typo_aliases.items():
        mapped = name_index.get(canonical)
        if mapped:
            name_index[typo] = mapped

    text_sources = [
        str(item.get("description") or ""),
        str(item.get("how_to_use") or ""),
        str(((item.get("details") or {}).get("extra") or {}).get("effects") or ""),
        str(((item.get("details") or {}).get("extra") or {}).get("drawbacks") or ""),
    ]
    text = " ".join(part for part in text_sources if part)

    candidates: list[str] = []

    fusion_match = re.search(
        r"fusion\s+between\s+(?:the\s+)?([^,.;]+?)\s+and\s+(?:the\s+)?([^,.;]+)",
        text,
        flags=re.IGNORECASE,
    )
    if fusion_match:
        candidates.extend([fusion_match.group(1), fusion_match.group(2)])

    successor_match = re.search(
        r"successor\s+(?:to|of)\s+(?:the\s+)?([^,.;]+)",
        text,
        flags=re.IGNORECASE,
    )
    if successor_match:
        candidates.append(successor_match.group(1))

    evolution_match = re.search(
        r"evolution\s+of\s+(?:the\s+)?([^,.;]+)",
        text,
        flags=re.IGNORECASE,
    )
    if evolution_match:
        candidates.append(evolution_match.group(1))

    predecessor_match = re.search(
        r"predecessor\s+(?:of|to)?\s*(?:variant)?\s*(?:is|was)?\s*(?:the\s+)?([^,.;]+)",
        text,
        flags=re.IGNORECASE,
    )
    if predecessor_match:
        candidates.append(predecessor_match.group(1))

    if not candidates:
        related_items = ((item.get("synergies") or {}).get("related_items") or [])
        for entry in related_items:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("normalized_name") or "").strip()
            if name:
                candidates.append(name)

    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in candidates:
        token = normalize_lookup(str(raw))
        if not token:
            continue
        token = _clean_progression_fragment(token)
        if token in {"its predecessor", "predecessor", "the predecessor", "successor", "its predecessor variant"}:
            continue
        if target_name and token == target_name:
            continue
        token = _resolve_progression_token(token, name_index)
        if not token:
            continue
        token = token.strip()
        if not token or token in seen:
            continue
        seen.add(token)
        cleaned.append(token)

    if len(cleaned) > 4:
        cleaned = cleaned[:4]
    return tuple(cleaned)


def _resolve_progression_token(token: str, name_index: dict[str, str]) -> str:
    if token in name_index:
        return name_index[token]

    compact = token.replace(" ", "")
    if compact in name_index:
        return name_index[compact]

    variants = [token, compact, token.replace("schrodinger", "schrodinger"), token.replace("schrodinger", "s chrodinger")]
    for candidate in variants:
        if candidate in name_index:
            return name_index[candidate]

    for key, normalized_name in name_index.items():
        if token == key:
            return normalized_name
        if token in key and len(token) >= 6:
            return normalized_name
    return token


def _clean_progression_fragment(token: str) -> str:
    out = token.strip(" -_")
    out = re.sub(r"\b(?:the|an|a)\b", " ", out)
    out = re.sub(r"\b(?:it|its)\b", " ", out)
    out = re.sub(r"\b(?:sucessor|successor|predecessor|variant)\b", " ", out)
    out = re.sub(r"\s+", " ", out).strip()
    return out


def _resolve_name(name: str, matcher: ItemMatcher) -> str | None:
    normalized = normalize_lookup(name)
    if not normalized:
        return None

    direct = matcher.alias_map.get(normalized)
    if direct:
        return direct[0]

    alias, score = matcher.probe(normalized)
    if alias and score >= 85:
        names = matcher.alias_map.get(alias)
        if names:
            return names[0]
    return None


def _split_name_count(chunk: str) -> tuple[str, int]:
    text = chunk.strip()
    if not text:
        return ("", 0)

    match = re.match(r"^(.*?)\s*[xX]\s*(\d+)\s*$", text)
    if match:
        return (match.group(1).strip(), max(1, int(match.group(2))))

    match = re.match(r"^(\d+)\s*[xX]?\s+(.+)$", text)
    if match:
        return (match.group(2).strip(), max(1, int(match.group(1))))

    match = re.match(r"^(.*?)\s*\((\d+)\)\s*$", text)
    if match:
        return (match.group(1).strip(), max(1, int(match.group(2))))

    return (text, 1)


def _as_float(value: Any, default: float | None = None) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return default
    return default


def _collect_unmodeled_effects(upgraders: list[UpgraderPick]) -> list[str]:
    seen: set[str] = set()
    for item in upgraders:
        for effect in item.unmodeled_effects:
            seen.add(effect.replace("_", " "))
    return sorted(seen)
