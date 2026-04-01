from __future__ import annotations

import html
import re
import unicodedata
from typing import Iterable

import mwparserfromhell


_NON_ALNUM_RE = re.compile(r"[^a-z0-9 ]+")
_SPACE_RE = re.compile(r"\s+")


def normalize_lookup(text: str) -> str:
    value = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    value = value.lower().replace("_", " ")
    value = _NON_ALNUM_RE.sub(" ", value)
    value = _SPACE_RE.sub(" ", value).strip()
    return value


def clean_wikitext(value: str) -> str:
    code = mwparserfromhell.parse(value)
    text = code.strip_code(normalize=True, collapse=True).strip()
    text = html.unescape(text)
    text = _SPACE_RE.sub(" ", text).strip()
    return text


def extract_x_values(text: str) -> list[float]:
    values: list[float] = []
    patterns = [
        re.compile(r"x\s*([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE),
        re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*x", re.IGNORECASE),
    ]
    for pattern in patterns:
        for match in pattern.findall(text):
            try:
                number = float(match)
            except ValueError:
                continue
            values.append(number)
    deduped: list[float] = []
    seen: set[float] = set()
    for value in values:
        if value in seen:
            continue
        deduped.append(value)
        seen.add(value)
    return deduped


def parse_x_value(text: str) -> tuple[str, float] | None:
    values = extract_x_values(text)
    if not values:
        return None
    value = values[0]
    formatted = format_float(value)
    return (f"x{formatted}", value)


def format_float(value: float) -> str:
    if value.is_integer():
        return str(int(value))
    return f"{value:.3f}".rstrip("0").rstrip(".")


def parse_int(text: str) -> int | None:
    match = re.search(r"-?\d+", text)
    if not match:
        return None
    try:
        return int(match.group(0))
    except ValueError:
        return None


def parse_yes_no(text: str) -> bool | None:
    norm = normalize_lookup(text)
    if norm in {"yes", "true"}:
        return True
    if norm in {"no", "false"}:
        return False
    return None


def first_non_empty(items: Iterable[str | None]) -> str | None:
    for item in items:
        if item and item.strip():
            return item.strip()
    return None


def make_aliases(name: str) -> list[str]:
    raw_aliases = {
        name,
        name.replace("_", " "),
        name.replace("'", ""),
        name.replace("-", " "),
        name.replace("&", "and"),
    }
    normalized = {normalize_lookup(alias) for alias in raw_aliases if alias.strip()}
    return sorted(alias for alias in normalized if alias)
