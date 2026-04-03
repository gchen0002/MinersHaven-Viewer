from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace
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
    multiplier: float
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
    related_items: tuple[str, ...]
    throughput_multiplier: float | None
    tile_footprint: int


@dataclass(slots=True)
class CalculationResult:
    mine_picks: list[MinePick]
    upgrader_picks: list[UpgraderPick]
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
    phase_breakdown: dict[str, list[str]]
    synergy_score: float
    target_seconds: float | None
    target_value: float | None
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
            phase_breakdown={"pre": [], "core": [], "post": [], "output": []},
            synergy_score=1.0,
            target_seconds=None,
            target_value=target_value,
            used_tiles=0,
            notes=notes,
        )

    mine_candidates.sort(key=lambda item: item.expected_value_per_second * item.confidence, reverse=True)
    selected_mines = mine_candidates[: max(1, min(3, int(max_mines)))]

    upgrader_candidates, excluded_cell_furnaces = _collect_upgrader_candidates(items, counts)
    if excluded_cell_furnaces > 0:
        notes.append(
            f"Excluded {excluded_cell_furnaces} cell furnace item(s) from upgrader chain (raw-ore only behavior)"
        )
    selected_upgraders, legality_notes = _select_legal_upgrader_chain(
        upgrader_candidates,
        max_items=max_upgraders,
        loop_cap=loop_cap,
        allow_destroy_items=allow_destroy_items,
    )
    notes.extend(legality_notes)

    mine_vps = sum(item.expected_value_per_second for item in selected_mines)
    base_ores_per_second = sum(item.ore_per_second or 0.0 for item in selected_mines)

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

    target_seconds: float | None = None
    if use_target_mode and target_value is not None and estimated_total_vps > 0:
        target_seconds = target_value / estimated_total_vps

    ordered = _order_chain_for_pipeline(selected_upgraders)
    phases = _split_pipeline_phases(ordered)
    phase_breakdown = {
        "pre": [item.name for item in phases["pre"]],
        "core": [item.name for item in phases["core"]],
        "post": [item.name for item in phases["post"]],
        "output": [item.name for item in phases["output"]],
    }

    return CalculationResult(
        mine_picks=selected_mines,
        upgrader_picks=selected_upgraders,
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
        phase_breakdown=phase_breakdown,
        synergy_score=synergy_multiplier,
        target_seconds=target_seconds,
        target_value=target_value,
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
) -> tuple[list[UpgraderPick], int]:
    candidates: list[UpgraderPick] = []
    excluded_cell_furnaces = 0
    for normalized_name, count in counts.items():
        item = items.get(normalized_name)
        if not item:
            continue
        item_type = normalize_lookup(str(item.get("type") or ""))
        if item_type not in {"upgrader", "furnace"}:
            continue

        pick = _upgrader_pick_from_item(normalized_name, item)
        if pick is None:
            if _is_cell_furnace_item(item, normalized_name):
                excluded_cell_furnaces += max(0, count)
            continue
        replicated = min(max(0, count), 30)
        for _ in range(replicated):
            candidates.append(pick)
    return (candidates, excluded_cell_furnaces)


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


def _upgrader_pick_from_item(normalized_name: str, item: dict[str, Any]) -> UpgraderPick | None:
    item_type = normalize_lookup(str(item.get("type") or ""))
    if item_type == "furnace" and _is_cell_furnace_item(item, normalized_name):
        return None

    multiplier = item.get("multiplier") or {}
    multiplier_value = _as_float(multiplier.get("value"))
    if multiplier_value is None or multiplier_value <= 1:
        return None

    effects = item.get("effects") or {}
    behaviors = set(effects.get("behaviors") or [])
    effect_tags = set((item.get("effect_tags") or {}).keys())
    proof_limits = item.get("proof_and_limits") or {}
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
    effects_status = tuple(sorted({normalize_lookup(str(token)) for token in (effects.get("status_effects") or []) if token}))

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

    size = item.get("size") or {}
    width = int(size.get("width") or 1)
    length = int(size.get("length") or 1)
    tile_footprint = max(1, width * length)

    return UpgraderPick(
        normalized_name=normalized_name,
        name=str(item.get("name") or normalized_name),
        item_type=item_type,
        multiplier=multiplier_value,
        confidence=max(0.1, min(1.0, conf)),
        mpu_cap=mpu_cap,
        is_resetter=("resetter" in behaviors) or bool(proof_limits.get("can_reset")),
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
        related_items=tuple(token for token in related_items if token),
        throughput_multiplier=throughput_multiplier,
        tile_footprint=tile_footprint,
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

    available_resetters = sum(1 for item in ordered if item.is_resetter)
    loop_cap = max(1, int(loop_cap))
    passes = 1 if available_resetters <= 0 else min(loop_cap, 1 + available_resetters)

    chain = 1.0
    for pass_idx in range(passes):
        pass_diminish = 0.88**pass_idx
        pass_mult = 1.0
        for item in phases["pre"] + phases["core"] + phases["post"] + phases["output"]:
            local_mult = 1.0 + (max(1.0, item.multiplier) - 1.0) * pass_diminish
            local_mult = 1.0 + (local_mult - 1.0) * item.confidence

            if item.randomized:
                local_mult = 1.0 + (local_mult - 1.0) * 0.85
            if item.requires_condition:
                local_mult = 1.0 + (local_mult - 1.0) * 0.9
            if item.excludes_condition:
                local_mult = 1.0 + (local_mult - 1.0) * 0.94
            if item.directional:
                local_mult = 1.0 + (local_mult - 1.0) * 0.92
            if item.works_with:
                local_mult *= 1.02

            pass_mult *= local_mult
            if pass_mult > 1e9:
                pass_mult = 1e9
                break

        chain *= pass_mult
        if chain > 1e18:
            chain = 1e18
            break

    uncapped_chain = max(1.0, chain)
    mpu_caps = [item.mpu_cap for item in ordered if item.mpu_cap is not None and item.mpu_cap > 1]
    applied_cap: float | None = None
    if mpu_caps:
        min_mpu = min(mpu_caps)
        if chain > min_mpu:
            chain = min_mpu
            applied_cap = min_mpu
            notes.append(f"MPU cap applied at x{min_mpu:g}")

    notes.append(
        f"Ordered pipeline simulation: pre={len(phases['pre'])}, core={len(phases['core'])}, "
        f"post={len(phases['post'])}, output={len(phases['output'])}; passes={passes}"
    )
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

    floor = min(pre_floor, core_floor, post_floor, out_floor)
    bottleneck = max(0.05, min(5.0, floor * teleporter_tax * splitter_tax))

    notes.append(
        f"Ordered throughput floors pre/core/post/output = {pre_floor:.2f}/{core_floor:.2f}/{post_floor:.2f}/{out_floor:.2f}"
    )
    if teleporter_tax < 1.0 or splitter_tax < 1.0:
        notes.append(f"Topology throughput tax applied: teleporter={teleporter_tax:.2f}, split_merge={splitter_tax:.2f}")

    return (bottleneck, notes)


def _order_chain_for_pipeline(upgraders: list[UpgraderPick]) -> list[UpgraderPick]:
    def stage_rank(item: UpgraderPick) -> int:
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


def _is_cell_furnace_item(item: dict[str, Any], normalized_name: str) -> bool:
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

    return base + reset_bonus + throughput_bonus + topology_bonus - destroy_penalty - condition_penalty


def _combined_confidence(a: float | None, b: float | None) -> float:
    left = 0.7 if a is None else a
    right = 0.7 if b is None else b
    return max(0.05, min(1.0, left * right))


def _select_legal_upgrader_chain(
    candidates: list[UpgraderPick],
    max_items: int,
    loop_cap: int,
    allow_destroy_items: bool,
) -> tuple[list[UpgraderPick], list[str]]:
    if not candidates:
        return ([], ["No upgrader/furnace candidates available"])

    ordered = sorted(candidates, key=_upgrader_priority, reverse=True)
    selected: list[UpgraderPick] = []
    notes: list[str] = []

    resetters = 0
    teleporters = 0
    splitters = 0
    mergers = 0
    furnaces = 0

    max_items = max(1, int(max_items))
    loop_cap = max(1, int(loop_cap))
    max_resetters = max(1, loop_cap - 1)
    max_teleporters = 4
    max_splitters = 3
    max_mergers = 3
    max_furnaces = 2

    destroy_filtered_out = 0

    for candidate in ordered:
        if len(selected) >= max_items:
            break

        if candidate.is_resetter and resetters >= max_resetters:
            continue
        if candidate.is_teleporter and teleporters >= max_teleporters:
            continue
        if candidate.is_splitter and splitters >= max_splitters:
            continue
        if candidate.is_merger and mergers >= max_mergers:
            continue
        if candidate.is_furnace and furnaces >= max_furnaces:
            continue

        if not allow_destroy_items and candidate.destroys_ore:
            destroy_filtered_out += 1
            continue

        if candidate.destroys_ore:
            current_destroys = sum(1 for item in selected if item.destroys_ore)
            if current_destroys >= 5:
                continue

        if candidate.requires_condition:
            if sum(1 for item in selected if item.requires_condition) >= 8:
                continue

        selected.append(candidate)
        if candidate.is_resetter:
            resetters += 1
        if candidate.is_teleporter:
            teleporters += 1
        if candidate.is_splitter:
            splitters += 1
        if candidate.is_merger:
            mergers += 1
        if candidate.is_furnace:
            furnaces += 1

    if not selected:
        fallback = ordered[: min(max_items, 4)]
        selected = [replace(item) for item in fallback]
        notes.append("Fell back to top-ranked chain due to strict legality filters")

    notes.append(
        "Chain legality filters: "
        f"resetters<={max_resetters}, teleporters<={max_teleporters}, splitters<={max_splitters}, mergers<={max_mergers}"
    )
    if not allow_destroy_items:
        notes.append(f"No-destroy mode enabled; filtered {destroy_filtered_out} destroy item candidate(s)")
    return (selected, notes)


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
