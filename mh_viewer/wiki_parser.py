from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import mwparserfromhell

from .utils import (
    clean_wikitext,
    extract_x_values,
    first_non_empty,
    make_aliases,
    normalize_lookup,
    parse_int,
    parse_x_value,
    parse_yes_no,
)
from .wiki_client import WikiPage


PARSER_VERSION = "1.0.0"
ELEMENT_NAMES = ["aether", "water", "earth", "fire", "order", "entropy"]
SIZE_CATEGORIES = ["tiny", "small", "medium", "large", "huge"]


def parse_wiki_page(page: WikiPage, preferred_type: str | None = None) -> dict[str, Any]:
    code = mwparserfromhell.parse(page.content)
    infobox_raw = _extract_infobox_params(code)
    infobox_clean = {key: clean_wikitext(value) for key, value in infobox_raw.items()}

    categories = _extract_categories(page.content)
    item_type = _infer_item_type(categories, preferred_type)
    quote_text = _extract_quote(code)
    overview_text = _extract_overview(page.content)
    effects_text = infobox_clean.get("effects", "")
    drawbacks_text = infobox_clean.get("drawbacks", "")

    description = first_non_empty([quote_text, _first_sentence(overview_text), effects_text]) or ""
    how_to_use = first_non_empty([overview_text, quote_text, effects_text]) or ""

    size = _parse_size(infobox_raw.get("item_size"), categories)
    multiplier = _parse_primary_multiplier(effects_text)
    mpu = _parse_mpu(infobox_clean.get("multiplier_length", ""))
    elements = _parse_elements(infobox_raw.get("elements", ""))

    tags = {
        "source": infobox_clean.get("source"),
        "requirements": infobox_clean.get("requirements"),
        "life_required": infobox_clean.get("life_required"),
        "rarity": _coerce_numeric_or_text(infobox_clean.get("rarity")),
        "conveyor_speed": infobox_clean.get("conveyor_speed"),
        "conveyor_wall": infobox_clean.get("conveyor_wall"),
        "conveyor_elevation": infobox_clean.get("conveyor_elevation"),
        "upgrade_limit": _coerce_numeric_or_text(infobox_clean.get("upgrade_limit")),
        "upgrade_counter": _coerce_numeric_or_text(infobox_clean.get("upgrade_counter")),
        "reborn_proof": parse_yes_no(infobox_clean.get("reborn_proof", "")),
        "sacrifice_proof": parse_yes_no(infobox_clean.get("sacrifice_proof", "")),
        "cost": infobox_clean.get("cost"),
        "sell_yield": infobox_clean.get("sell"),
        "item_id": _coerce_numeric_or_text(infobox_clean.get("itemid") or infobox_clean.get("item_id")),
    }
    tags = {key: value for key, value in tags.items() if value not in (None, "")}

    effect_tags = _extract_effect_tags(effects_text, drawbacks_text, overview_text, categories)
    proof_and_limits = {
        "reborn_proof": tags.get("reborn_proof"),
        "sacrifice_proof": tags.get("sacrifice_proof"),
        "upgrade_limit": tags.get("upgrade_limit"),
        "upgrade_counter": tags.get("upgrade_counter"),
        "can_reset": _detect_can_reset(infobox_raw.get("upgrade_limit", "")),
    }
    proof_and_limits = {key: value for key, value in proof_and_limits.items() if value is not None}

    acquisition = {
        "source": tags.get("source"),
        "requirements": tags.get("requirements"),
        "life_required": tags.get("life_required"),
        "rarity": tags.get("rarity"),
        "limited": _contains_category(categories, "limited"),
        "event": any("202" in cat for cat in categories) or _contains_category(categories, "events"),
    }
    acquisition = {key: value for key, value in acquisition.items() if value is not None}

    multipliers = {
        "raw_effects": effects_text,
        "x_values": extract_x_values(" ".join(part for part in [effects_text, quote_text, overview_text] if part)),
        "mpu": mpu,
    }

    normalized_name = normalize_lookup(page.title)
    aliases = _build_aliases(page.title, infobox_clean)

    return {
        "name": page.title,
        "normalized_name": normalized_name,
        "aliases": aliases,
        "type": item_type,
        "tier": infobox_clean.get("tier"),
        "description": description,
        "how_to_use": how_to_use,
        "multiplier": multiplier,
        "size": size,
        "mpu": mpu,
        "effect_tags": effect_tags,
        "proof_and_limits": proof_and_limits,
        "acquisition": acquisition,
        "wiki_url": _wiki_url(page.title),
        "details": {
            "tags": tags,
            "elements": elements,
            "categories": categories,
            "extra": infobox_clean,
            "multipliers": multipliers,
        },
        "metadata": {
            "wiki_title": page.title,
            "pageid": page.pageid,
            "revid": page.revid,
            "last_synced_at": datetime.now(tz=timezone.utc).isoformat(),
            "parser_version": PARSER_VERSION,
        },
    }


def _extract_infobox_params(code: mwparserfromhell.wikicode.Wikicode) -> dict[str, str]:
    for template in code.filter_templates(recursive=True):
        name = _canonical_template_name(str(template.name))
        if name != "newinfobox":
            continue
        params: dict[str, str] = {}
        for param in template.params:
            key = _canonical_key(str(param.name))
            value = str(param.value).strip()
            if not key:
                continue
            params[key] = value
        return params
    return {}


def _extract_quote(code: mwparserfromhell.wikicode.Wikicode) -> str:
    for template in code.filter_templates(recursive=True):
        if _canonical_template_name(str(template.name)) != "quote":
            continue
        if not template.params:
            continue
        return clean_wikitext(str(template.params[0].value))
    return ""


def _extract_overview(raw: str) -> str:
    match = re.search(r"==\s*Overview\s*==(?P<body>.*?)(\n==[^=].*?==|\Z)", raw, re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    return clean_wikitext(match.group("body"))


def _extract_categories(raw: str) -> list[str]:
    matches = re.findall(r"\[\[Category:([^\]|]+)", raw, flags=re.IGNORECASE)
    deduped: list[str] = []
    seen: set[str] = set()
    for match in matches:
        value = match.strip()
        key = normalize_lookup(value)
        if not value or key in seen:
            continue
        deduped.append(value)
        seen.add(key)
    return deduped


def _infer_item_type(categories: list[str], preferred_type: str | None) -> str | None:
    if _contains_category(categories, "upgrader"):
        return "Upgrader"
    if _contains_category(categories, "furnace"):
        return "Furnace"
    if preferred_type:
        return preferred_type
    return None


def _contains_category(categories: list[str], keyword: str) -> bool:
    needle = normalize_lookup(keyword)
    return any(needle in normalize_lookup(category) for category in categories)


def _parse_size(raw_value: str | None, categories: list[str]) -> dict[str, Any] | None:
    if not raw_value:
        size_category = None
        for category in categories:
            cat = normalize_lookup(category)
            if cat in SIZE_CATEGORIES:
                size_category = category
                break
        if size_category:
            return {"category": size_category}
        return None

    footprint: str | None = None
    width: int | None = None
    length: int | None = None
    height: int | None = None

    parsed = mwparserfromhell.parse(raw_value)
    for template in parsed.filter_templates(recursive=True):
        if _canonical_template_name(str(template.name)) != "itemsize":
            continue
        if template.has(1):
            footprint = clean_wikitext(str(template.get(1).value))
            dims = re.search(r"(\d+)\s*x\s*(\d+)", footprint, flags=re.IGNORECASE)
            if dims:
                width = int(dims.group(1))
                length = int(dims.group(2))
        if template.has(2):
            height = parse_int(clean_wikitext(str(template.get(2).value)))
        break

    if not footprint:
        text = clean_wikitext(raw_value)
        dims = re.search(r"(\d+)\s*x\s*(\d+)", text, flags=re.IGNORECASE)
        if dims:
            width = int(dims.group(1))
            length = int(dims.group(2))
            footprint = f"{width}x{length}"
        if height is None:
            height_match = re.search(r"height\s*:?\s*(\d+)", text, flags=re.IGNORECASE)
            if height_match:
                height = int(height_match.group(1))

    if not footprint and width is not None and length is not None:
        footprint = f"{width}x{length}"

    size_category = None
    for category in categories:
        cat = normalize_lookup(category)
        if cat in SIZE_CATEGORIES:
            size_category = category
            break

    payload: dict[str, Any] = {}
    if footprint:
        payload["footprint"] = footprint
    if width is not None:
        payload["width"] = width
    if length is not None:
        payload["length"] = length
    if height is not None:
        payload["height"] = height
    if size_category:
        payload["category"] = size_category
    return payload or None


def _parse_primary_multiplier(effects_text: str) -> dict[str, Any] | None:
    parsed = parse_x_value(effects_text)
    if not parsed:
        return None
    text, value = parsed
    return {"text": text, "value": value}


def _parse_mpu(raw_value: str) -> dict[str, Any] | None:
    if not raw_value:
        return None
    parsed = parse_x_value(raw_value)
    if not parsed:
        return {"text": raw_value, "value": None}
    text, value = parsed
    return {"text": text, "value": value}


def _parse_elements(raw_value: str) -> dict[str, Any] | None:
    if not raw_value:
        return None
    parsed = mwparserfromhell.parse(raw_value)
    for template in parsed.filter_templates(recursive=True):
        if _canonical_template_name(str(template.name)) != "elements":
            continue
        values: list[int] = []
        for index in range(1, 7):
            if not template.has(index):
                values.append(0)
                continue
            value = parse_int(clean_wikitext(str(template.get(index).value)))
            values.append(0 if value is None else value)
        return {
            "raw": values,
            "named": {name: values[i] for i, name in enumerate(ELEMENT_NAMES)},
        }

    text = clean_wikitext(raw_value)
    ints = re.findall(r"-?\d+", text)
    if len(ints) < 6:
        return None
    values = [int(item) for item in ints[:6]]
    return {
        "raw": values,
        "named": {name: values[i] for i, name in enumerate(ELEMENT_NAMES)},
    }


def _extract_effect_tags(effects: str, drawbacks: str, overview: str, categories: list[str]) -> dict[str, bool]:
    joined = normalize_lookup(" ".join([effects, drawbacks, overview, " ".join(categories)]))
    tags = {
        "sets_fire": "fire" in joined,
        "freezes": "frost" in joined or "frozen" in joined,
        "poisons": "poison" in joined,
        "radioactive": "radioactive" in joined,
        "wet": "wet" in joined,
        "anti_gravity": "anti gravity" in joined,
        "destroys_ore": "destroy" in joined,
        "scanner": "scanner" in joined,
    }
    return {key: value for key, value in tags.items() if value}


def _detect_can_reset(upgrade_limit_raw: str) -> bool:
    text = normalize_lookup(clean_wikitext(upgrade_limit_raw))
    raw = normalize_lookup(upgrade_limit_raw)
    return "tesla" in raw or "resetting" in text


def _build_aliases(title: str, infobox_clean: dict[str, str]) -> list[str]:
    aliases = set(make_aliases(title))
    submission_name = infobox_clean.get("submission_name")
    if submission_name:
        aliases.add(normalize_lookup(submission_name))
    for alias in list(aliases):
        if alias.startswith("the ") and len(alias) > 4:
            aliases.add(alias[4:])
    return sorted(alias for alias in aliases if alias)


def _coerce_numeric_or_text(value: str | None) -> int | float | str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    if re.fullmatch(r"-?\d+", cleaned):
        return int(cleaned)
    if re.fullmatch(r"-?\d+\.\d+", cleaned):
        return float(cleaned)
    number = parse_int(cleaned)
    if number is not None and len(cleaned) <= 4:
        return number
    return cleaned


def _first_sentence(text: str) -> str:
    if not text:
        return ""
    chunks = re.split(r"(?<=[.!?])\s+", text)
    return chunks[0].strip() if chunks else text


def _wiki_url(title: str) -> str:
    encoded = quote(title.replace(" ", "_"), safe="_")
    return f"https://minershaven.fandom.com/wiki/{encoded}"


def _canonical_template_name(name: str) -> str:
    return normalize_lookup(name).replace(" ", "")


def _canonical_key(name: str) -> str:
    value = normalize_lookup(name).replace(" ", "_")
    value = value.strip("_")
    value = value.rstrip(":")
    if value == "itemid":
        return "item_id"
    return value
