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
    is_teleporter: bool
    is_splitter: bool
    is_merger: bool
    directional: bool
    randomized: bool
    requires_condition: bool
    excludes_condition: bool
    works_with: bool
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
            target_seconds=None,
            target_value=target_value,
            used_tiles=0,
            notes=notes,
        )

    mine_candidates.sort(key=lambda item: item.expected_value_per_second * item.confidence, reverse=True)
    selected_mines = mine_candidates[: max(1, min(3, int(max_mines)))]

    upgrader_candidates = _collect_upgrader_candidates(items, counts)
    selected_upgraders, legality_notes = _select_legal_upgrader_chain(
        upgrader_candidates,
        max_items=max_upgraders,
        loop_cap=loop_cap,
    )
    notes.extend(legality_notes)

    mine_vps = sum(item.expected_value_per_second for item in selected_mines)
    base_ores_per_second = sum(item.ore_per_second or 0.0 for item in selected_mines)

    bottleneck_multiplier = _estimate_bottleneck_multiplier(selected_upgraders)
    effective_ores_per_second = base_ores_per_second
    if base_ores_per_second > 0:
        effective_ores_per_second = base_ores_per_second * min(1.0, bottleneck_multiplier)
    throughput_ratio = 1.0 if base_ores_per_second <= 0 else max(0.05, effective_ores_per_second / base_ores_per_second)
    adjusted_mine_vps = mine_vps * throughput_ratio

    estimated_multiplier, loop_passes, mult_note = _simulate_chain_multiplier(selected_upgraders, loop_cap)
    if mult_note:
        notes.append(mult_note)

    destruction_penalty = _destruction_penalty(selected_upgraders)
    if destruction_penalty < 1.0:
        notes.append("Applied ore-destruction reliability penalty")

    estimated_total_vps = adjusted_mine_vps * estimated_multiplier * destruction_penalty

    if bottleneck_multiplier < 1.0:
        notes.append(f"Conveyor bottleneck detected at x{bottleneck_multiplier:.2f}")
    elif bottleneck_multiplier > 1.0:
        notes.append(f"Conveyor capacity above source rate (x{bottleneck_multiplier:.2f})")

    dynamic_mines = sum(1 for item in selected_mines if item.confidence < 0.5)
    if dynamic_mines:
        notes.append(f"{dynamic_mines} selected mine(s) rely on dynamic value formulas")

    target_seconds: float | None = None
    if use_target_mode and target_value is not None and estimated_total_vps > 0:
        target_seconds = target_value / estimated_total_vps

    used_tiles = sum(item.tile_footprint for item in selected_mines) + sum(item.tile_footprint for item in selected_upgraders)

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


def _collect_upgrader_candidates(items: dict[str, dict[str, Any]], counts: dict[str, int]) -> list[UpgraderPick]:
    candidates: list[UpgraderPick] = []
    for normalized_name, count in counts.items():
        item = items.get(normalized_name)
        if not item:
            continue
        item_type = normalize_lookup(str(item.get("type") or ""))
        if item_type not in {"upgrader", "furnace"}:
            continue

        pick = _upgrader_pick_from_item(normalized_name, item)
        if pick is None:
            continue
        replicated = min(max(0, count), 30)
        for _ in range(replicated):
            candidates.append(pick)
    return candidates


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
    multiplier = item.get("multiplier") or {}
    multiplier_value = _as_float(multiplier.get("value"))
    if multiplier_value is None or multiplier_value <= 1:
        return None

    item_type = normalize_lookup(str(item.get("type") or ""))
    effects = item.get("effects") or {}
    behaviors = set(effects.get("behaviors") or [])
    effect_tags = set((item.get("effect_tags") or {}).keys())
    proof_limits = item.get("proof_and_limits") or {}
    synergies = item.get("synergies") or {}
    synergy_keywords = set(synergies.get("keywords") or [])

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
        is_teleporter="teleporter" in behaviors,
        is_splitter="splitter" in behaviors,
        is_merger="merger" in behaviors,
        directional="directional" in behaviors,
        randomized="randomized" in behaviors,
        requires_condition="requires_condition" in synergy_keywords,
        excludes_condition="excludes_condition" in synergy_keywords,
        works_with="works_with" in synergy_keywords,
        throughput_multiplier=throughput_multiplier,
        tile_footprint=tile_footprint,
    )


def _simulate_chain_multiplier(upgraders: list[UpgraderPick], loop_cap: int) -> tuple[float, int, str]:
    if not upgraders:
        return (1.0, 1, "No static upgrader multipliers found; using x1 baseline")

    available_resetters = sum(1 for item in upgraders if item.is_resetter)
    loop_cap = max(1, int(loop_cap))
    passes = 1 if available_resetters <= 0 else min(loop_cap, 1 + available_resetters)

    chain = 1.0
    for pass_idx in range(passes):
        pass_diminish = 0.88**pass_idx
        pass_mult = 1.0
        for item in upgraders:
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

    mpu_caps = [item.mpu_cap for item in upgraders if item.mpu_cap is not None and item.mpu_cap > 1]
    note = f"Simulated {passes} pass(es) with diminishing loop factor"
    if mpu_caps:
        min_mpu = min(mpu_caps)
        if chain > min_mpu:
            chain = min_mpu
            note += f"; capped by MPU x{min_mpu:g}"

    return (max(1.0, chain), passes, note)


def _estimate_bottleneck_multiplier(upgraders: list[UpgraderPick]) -> float:
    throughput_values = [item.throughput_multiplier for item in upgraders if item.throughput_multiplier is not None]
    if not throughput_values:
        return 1.0
    floor = min(throughput_values)
    teleporter_tax = 1.0 - min(0.18, 0.04 * sum(1 for item in upgraders if item.is_teleporter))
    splitter_tax = 1.0 - min(0.2, 0.06 * sum(1 for item in upgraders if item.is_splitter or item.is_merger))
    return max(0.05, min(5.0, floor * teleporter_tax * splitter_tax))


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
