from __future__ import annotations

import argparse
import ctypes
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from pynput import keyboard, mouse

from .cache import CacheStore
from .config import AppConfig, Region, load_config
from .matcher import ItemMatcher
from .ocr_engine import OcrEngine
from .sync import WikiSyncService
from .utils import normalize_lookup
from .viewer_ui import ViewerWindow


@dataclass(slots=True)
class HoverState:
    hold_pressed: bool = False
    hold_effective: bool = False
    last_candidate: str = ""
    stable_count: int = 0
    active_item: str = ""
    warmup_left: int = 0


def run() -> None:
    parser = _build_arg_parser()
    args = parser.parse_args()

    if args.planner:
        from .planner_app import run as run_planner

        run_planner()
        return

    cfg = load_config(Path(args.config) if args.config else None)
    store = CacheStore(cfg.cache_dir)
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
        print("Cache is empty; running initial sync...")
        result = sync_service.sync_all(force_full=True)
        print(
            f"Sync complete: total={result.total_titles} fetched={result.fetched_count} "
            f"skipped={result.skipped_count}"
        )
        snapshot = store.load()

    if not snapshot.items:
        print("No items found after sync, exiting.")
        sys.exit(1)

    run_viewer(cfg, snapshot.items, snapshot.index)


def run_viewer(cfg: AppConfig, items: dict[str, dict], index: dict) -> None:
    matcher = ItemMatcher.from_index(index)
    if matcher.is_empty():
        print("Index is empty. Run with --sync --full to rebuild cache.")
        sys.exit(1)

    try:
        ocr = OcrEngine(cfg.tesseract_cmd)
    except RuntimeError as error:
        print(str(error))
        sys.exit(1)

    ui = ViewerWindow()
    state = HoverState()
    state.warmup_left = max(0, cfg.warmup_cycles)
    mouse_controller = mouse.Controller()
    screen_width = ui.root.winfo_screenwidth()
    screen_height = ui.root.winfo_screenheight()

    hold_keys = _parse_hold_keys(cfg.hold_key)
    hold_vk_codes = _parse_hold_vk_codes(cfg.hold_key)

    def on_press(key: keyboard.Key | keyboard.KeyCode) -> None:
        if key in hold_keys:
            state.hold_pressed = True

    def on_release(key: keyboard.Key | keyboard.KeyCode) -> None:
        if key in hold_keys:
            state.hold_pressed = False

    listener = keyboard.Listener(on_press=on_press, on_release=on_release)
    listener.start()

    hold_hint = cfg.hold_key.upper()
    ui.show_status(f"Ready. Hold {hold_hint} while hovering an inventory item.")

    try:
        while True:
            ui.tick()
            cursor_x, cursor_y = mouse_controller.position
            key_pressed_by_vk = _any_vk_down(hold_vk_codes)
            hold_down = state.hold_pressed or key_pressed_by_vk

            if hold_down and not state.hold_effective:
                state.hold_effective = True
                state.warmup_left = max(0, cfg.warmup_cycles)
                state.stable_count = 0
                state.last_candidate = ""

            if not hold_down and state.hold_effective:
                state.hold_effective = False
                state.stable_count = 0
                state.last_candidate = ""
                state.warmup_left = max(0, cfg.warmup_cycles)

            if not hold_down:
                if cfg.debug_mode:
                    ui.set_debug(
                        f"hold=NO key={cfg.hold_key} listener={state.hold_pressed} "
                        f"vk={key_pressed_by_vk} cursor=({cursor_x},{cursor_y})"
                    )
                time.sleep(cfg.scan_interval_ms / 1000)
                continue

            required_inventory_text = cfg.inventory_text.strip().lower()
            if required_inventory_text:
                inventory_ocr = ocr.read_region(cfg.inventory_region, psm=7)
                if required_inventory_text not in inventory_ocr.text.lower():
                    if cfg.debug_mode:
                        ui.set_debug(
                            f"hold=YES inventory_gate=MISS text='{inventory_ocr.text[:28]}' "
                            f"cursor=({cursor_x},{cursor_y})"
                        )
                    time.sleep(cfg.scan_interval_ms / 1000)
                    continue

            if state.warmup_left > 0:
                state.warmup_left -= 1
                if cfg.debug_mode:
                    ui.set_debug(
                        f"hold=YES warmup={state.warmup_left} multi={'ON' if cfg.multi_region_scan else 'OFF'} "
                        f"cursor=({cursor_x},{cursor_y})"
                    )
                time.sleep(cfg.scan_interval_ms / 1000)
                continue

            if cfg.multi_region_scan:
                multi_result = _scan_multi_region(ocr, cfg, mouse_controller, screen_width, screen_height, matcher)
                if multi_result is None:
                    if cfg.debug_mode:
                        ui.set_debug(f"hold=YES multi_scan=NO_TEXT cursor=({cursor_x},{cursor_y})")
                    time.sleep(cfg.scan_interval_ms / 1000)
                    continue

                hover_ocr_text = multi_result.ocr_text
                hover_ocr_conf = multi_result.confidence
                alias = multi_result.alias
                probe_score = multi_result.probe_score
            else:
                hover_region = _resolve_hover_region(cfg, mouse_controller, screen_width, screen_height)

                hover_ocr = ocr.read_region(
                    hover_region,
                    psm=7,
                    whitelist="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789' -",
                )
                if not hover_ocr.text:
                    if cfg.debug_mode:
                        ui.set_debug(
                            f"hold=YES ocr=EMPTY conf={hover_ocr.confidence:.0f} "
                            f"region=({hover_region.left},{hover_region.top},{hover_region.width},{hover_region.height})"
                        )
                    time.sleep(cfg.scan_interval_ms / 1000)
                    continue

                hover_ocr_text = hover_ocr.text
                hover_ocr_conf = hover_ocr.confidence
                alias, probe_score = matcher.probe(hover_ocr.text)

            match = matcher.match(hover_ocr_text, min_score=cfg.min_match_score)
            if not match:
                if cfg.debug_mode:
                    ui.set_debug(
                        f"hold=YES ocr='{hover_ocr_text[:26]}' best='{alias or '-'}' "
                        f"score={probe_score}/{cfg.min_match_score}"
                    )
                time.sleep(cfg.scan_interval_ms / 1000)
                continue

            candidate = match.normalized_name
            if candidate == state.last_candidate:
                state.stable_count += 1
            else:
                state.last_candidate = candidate
                state.stable_count = 1

            required_frames = cfg.ocr_stability_frames
            if match.score >= 90:
                required_frames = 1
            elif match.score >= 80:
                required_frames = min(required_frames, 2)

            if state.stable_count < required_frames:
                if cfg.debug_mode:
                    ui.set_debug(
                        f"hold=YES candidate='{candidate}' stable={state.stable_count}/{required_frames} "
                        f"ocr='{hover_ocr_text[:18]}'"
                    )
                time.sleep(cfg.scan_interval_ms / 1000)
                continue

            if candidate != state.active_item:
                item = items.get(normalize_lookup(candidate)) or items.get(candidate)
                if item:
                    ui.update_item(item, score=match.score)
                    state.active_item = candidate
                    if cfg.debug_mode:
                        if cfg.multi_region_scan:
                            ui.set_debug(
                                f"MATCHED {item.get('name', candidate)} score={match.score} "
                                f"ocr='{hover_ocr_text[:24]}' variant={multi_result.variant_idx}/{multi_result.variant_total}"
                            )
                        else:
                            ui.set_debug(
                                f"MATCHED {item.get('name', candidate)} score={match.score} "
                                f"ocr='{hover_ocr_text[:24]}'"
                            )
            elif cfg.debug_mode:
                ui.set_debug(
                    f"hold=YES locked='{candidate}' score={match.score} ocr='{hover_ocr_text[:24]}'"
                )

            time.sleep(cfg.scan_interval_ms / 1000)
    except tk_closed_error:
        pass
    except KeyboardInterrupt:
        pass
    finally:
        listener.stop()


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Miners Haven inventory hover viewer")
    parser.add_argument("--config", help="Path to config.json")
    parser.add_argument("--sync", action="store_true", help="Sync cache before launching viewer")
    parser.add_argument("--sync-only", action="store_true", help="Only sync cache and exit")
    parser.add_argument("--full", action="store_true", help="Force full refresh from wiki")
    parser.add_argument("--planner", action="store_true", help="Launch inventory planner calculator UI")
    return parser


def _parse_hold_keys(name: str) -> set[keyboard.Key | keyboard.KeyCode]:
    lowered = name.lower()
    if lowered in {"alt", "option"}:
        return {keyboard.Key.alt_l, keyboard.Key.alt_r}
    if lowered in {"ctrl", "control"}:
        return {keyboard.Key.ctrl_l, keyboard.Key.ctrl_r}
    if lowered == "shift":
        return {keyboard.Key.shift_l, keyboard.Key.shift_r}

    key = getattr(keyboard.Key, lowered, None)
    if key is not None:
        return {key}
    if len(lowered) == 1:
        return {keyboard.KeyCode.from_char(lowered)}
    return {keyboard.Key.alt_l, keyboard.Key.alt_r}


def _parse_hold_vk_codes(name: str) -> list[int]:
    lowered = name.lower().strip()
    if lowered in {"alt", "option"}:
        return [0x12, 0xA4, 0xA5]
    if lowered in {"ctrl", "control"}:
        return [0x11, 0xA2, 0xA3]
    if lowered == "shift":
        return [0x10, 0xA0, 0xA1]
    if lowered == "alt_l":
        return [0xA4]
    if lowered == "alt_r":
        return [0xA5]
    if lowered == "ctrl_l":
        return [0xA2]
    if lowered == "ctrl_r":
        return [0xA3]
    if lowered == "shift_l":
        return [0xA0]
    if lowered == "shift_r":
        return [0xA1]
    if len(lowered) == 1:
        return [ord(lowered.upper())]
    return [0x12, 0xA4, 0xA5]


def _any_vk_down(codes: list[int]) -> bool:
    if not codes:
        return False
    user32 = ctypes.windll.user32
    for code in codes:
        if user32.GetAsyncKeyState(code) & 0x8000:
            return True
    return False


def _build_region_grid(
    cursor_x: int,
    cursor_y: int,
    base_region: Region,
    screen_w: int,
    screen_h: int,
) -> list[tuple[int, int, int, Region]]:
    step_x = max(30, int(base_region.width * 0.25))
    step_y = max(20, int(base_region.height * 0.25))

    offsets = [
        (0, 0),
        (-step_x, 0),
        (step_x, 0),
        (0, -step_y),
        (0, step_y),
        (-step_x, -step_y),
        (step_x, -step_y),
        (-step_x, step_y),
        (step_x, step_y),
    ]

    regions: list[tuple[int, int, int, Region]] = []
    for idx, (off_x, off_y) in enumerate(offsets):
        width = max(20, int(base_region.width))
        height = max(20, int(base_region.height))
        left = int(cursor_x + base_region.left + off_x)
        top = int(cursor_y + base_region.top + off_y)

        if left < 0:
            left = 0
        if top < 0:
            top = 0
        if left + width > screen_w:
            left = max(0, screen_w - width)
        if top + height > screen_h:
            top = max(0, screen_h - height)

        regions.append((idx, off_x, off_y, Region(left=left, top=top, width=width, height=height)))

    return regions


@dataclass(slots=True)
class MultiScanResult:
    region: Region
    ocr_text: str
    confidence: float
    alias: str | None
    probe_score: float
    variant_idx: int
    variant_total: int


def _scan_multi_region(
    ocr: OcrEngine,
    cfg: AppConfig,
    mouse_controller: mouse.Controller,
    screen_width: int,
    screen_height: int,
    matcher: ItemMatcher,
) -> MultiScanResult | None:
    if cfg.hover_use_cursor:
        cursor_x, cursor_y = mouse_controller.position
        region_grid = _build_region_grid(cursor_x, cursor_y, cfg.cursor_region, screen_width, screen_height)
    else:
        region_grid = [(0, 0, 0, _resolve_hover_region(cfg, mouse_controller, screen_width, screen_height))]

    whitelist = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789' -"
    best_result: MultiScanResult | None = None

    for idx, _off_x, _off_y, region in region_grid:
        ocr_result = ocr.read_region(region, psm=7, whitelist=whitelist)
        if not ocr_result.text:
            continue

        alias, probe_score = matcher.probe(ocr_result.text)
        if best_result is None or probe_score > best_result.probe_score:
            best_result = MultiScanResult(
                region=region,
                ocr_text=ocr_result.text,
                confidence=ocr_result.confidence,
                alias=alias,
                probe_score=probe_score,
                variant_idx=idx + 1,
                variant_total=len(region_grid),
            )

    return best_result


def _resolve_hover_region(
    cfg: AppConfig,
    mouse_controller: mouse.Controller,
    screen_width: int,
    screen_height: int,
) -> Region:
    if not cfg.hover_use_cursor:
        return cfg.hover_region

    cursor_x, cursor_y = mouse_controller.position
    width = max(20, int(cfg.cursor_region.width))
    height = max(20, int(cfg.cursor_region.height))
    left = int(cursor_x + cfg.cursor_region.left)
    top = int(cursor_y + cfg.cursor_region.top)

    if left < 0:
        left = 0
    if top < 0:
        top = 0
    if left + width > screen_width:
        left = max(0, screen_width - width)
    if top + height > screen_height:
        top = max(0, screen_height - height)

    return Region(left=left, top=top, width=width, height=height)


class _TkClosedError(Exception):
    pass


try:
    import tkinter as _tk

    tk_closed_error = _tk.TclError
except Exception:  # pragma: no cover
    tk_closed_error = _TkClosedError


if __name__ == "__main__":
    run()
