from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


DEFAULT_DATA_DIR = Path("data")
DEFAULT_CONFIG_PATH = Path("config.json")


@dataclass(slots=True)
class Region:
    left: int
    top: int
    width: int
    height: int


@dataclass(slots=True)
class AppConfig:
    hold_key: str
    hover_region: Region
    hover_use_cursor: bool
    cursor_region: Region
    inventory_region: Region
    inventory_text: str
    cache_dir: str
    scan_interval_ms: int
    ocr_stability_frames: int
    min_match_score: int
    debug_mode: bool
    warmup_cycles: int
    tesseract_cmd: str | None
    multi_region_scan: bool


def default_config() -> AppConfig:
    return AppConfig(
        hold_key="alt_l",
        hover_region=Region(left=610, top=295, width=360, height=70),
        hover_use_cursor=True,
        cursor_region=Region(left=-220, top=-120, width=440, height=160),
        inventory_region=Region(left=500, top=120, width=340, height=80),
        inventory_text="",
        cache_dir=str(DEFAULT_DATA_DIR),
        scan_interval_ms=150,
        ocr_stability_frames=3,
        min_match_score=78,
        debug_mode=True,
        warmup_cycles=2,
        tesseract_cmd=None,
        multi_region_scan=True,
    )


def _region_from_obj(obj: dict[str, Any]) -> Region:
    return Region(
        left=int(obj.get("left", 0)),
        top=int(obj.get("top", 0)),
        width=int(obj.get("width", 0)),
        height=int(obj.get("height", 0)),
    )


def load_config(path: Path | None = None) -> AppConfig:
    target = path or DEFAULT_CONFIG_PATH
    if not target.exists():
        cfg = default_config()
        save_config(cfg, target)
        return cfg

    raw = json.loads(target.read_text(encoding="utf-8"))
    defaults = default_config()
    return AppConfig(
        hold_key=str(raw.get("hold_key", defaults.hold_key)),
        hover_region=_region_from_obj(raw.get("hover_region", asdict(defaults.hover_region))),
        hover_use_cursor=bool(raw.get("hover_use_cursor", defaults.hover_use_cursor)),
        cursor_region=_region_from_obj(raw.get("cursor_region", asdict(defaults.cursor_region))),
        inventory_region=_region_from_obj(raw.get("inventory_region", asdict(defaults.inventory_region))),
        inventory_text=str(raw.get("inventory_text", defaults.inventory_text)),
        cache_dir=str(raw.get("cache_dir", defaults.cache_dir)),
        scan_interval_ms=int(raw.get("scan_interval_ms", defaults.scan_interval_ms)),
        ocr_stability_frames=int(raw.get("ocr_stability_frames", defaults.ocr_stability_frames)),
        min_match_score=int(raw.get("min_match_score", defaults.min_match_score)),
        debug_mode=bool(raw.get("debug_mode", defaults.debug_mode)),
        warmup_cycles=int(raw.get("warmup_cycles", defaults.warmup_cycles)),
        tesseract_cmd=raw.get("tesseract_cmd"),
        multi_region_scan=bool(raw.get("multi_region_scan", defaults.multi_region_scan)),
    )


def save_config(cfg: AppConfig, path: Path | None = None) -> None:
    target = path or DEFAULT_CONFIG_PATH
    payload = asdict(cfg)
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
