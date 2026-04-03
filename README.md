# Miners Haven Hover Viewer

Desktop companion viewer for Miners Haven that reads hovered inventory item names with OCR, matches against a local wiki cache, and shows useful item info in a top-right panel.

## Features

- Full wiki sync for `Category:Upgrader` and `Category:Furnace`
- Permanent JSON cache (`data/items.json`, `data/index.json`, `data/meta.json`)
- Incremental refresh using wiki revision IDs
- Hold-key trigger (`left alt` by default)
- Inventory-mode gate via OCR keyword detection (optional, disabled by default)
- Top-right viewer with core info plus a `More Details` section

## Captured data

- Core: name, type, tier, description, how-to-use, multiplier, size, MPU, wiki URL
- Extended: extracted `effects` profile and `synergies` links/keywords for planning algorithms
- Details:
  - elements (raw and named)
  - proof and limits (reborn proof, sacrifice proof, upgrade limit/counter, can reset)
  - acquisition info
  - effect tags
  - parsed effects profile (status effects, mechanics, x-values)
  - parsed synergies (related items + synergy keywords)
  - aliases/normalized name
  - raw infobox fields (future-proof)
  - metadata (pageid, revid, sync timestamps, parser version)

## Install

```bash
python -m pip install -r requirements.txt
```

You also need the Tesseract OCR engine installed on Windows.

- Download and install Tesseract (usually installs to `C:\Program Files\Tesseract-OCR\tesseract.exe`)
- If needed, set `tesseract_cmd` in `config.json`

## First sync

```bash
python -m mh_viewer.app --sync-only --full
```

This performs the full upgrader + furnace scan and writes cache files in `data/`.

## Run viewer

```bash
python -m mh_viewer.app
```

Or:

```bash
python main.py
```

## Optional startup sync

```bash
python -m mh_viewer.app --sync
```

## Config

`config.json` is auto-generated on first run.

- `hold_key`: key name for hold trigger (`alt_l` default)
- `hover_region`: fixed screen rectangle where item hover text appears
- `hover_use_cursor`: if `true`, OCR region follows mouse cursor
- `cursor_region`: offset rectangle from cursor when `hover_use_cursor` is true
- `inventory_region`: screen rectangle where inventory label appears
- `inventory_text`: optional OCR keyword to allow scans (blank disables this gate)
- `scan_interval_ms`: loop interval
- `ocr_stability_frames`: required repeated match frames before update
- `min_match_score`: fuzzy OCR match threshold
- `debug_mode`: show live OCR/match diagnostics in overlay
- `warmup_cycles`: short delay after hold key before OCR starts
- `tesseract_cmd`: optional path to `tesseract.exe`

### Region calibration notes

Current defaults are placeholders. For reliable detection:

1. Open Miners Haven inventory.
2. Keep `hover_use_cursor=true` to scan around the mouse automatically.
3. Adjust `cursor_region` to tightly cover your hover tooltip title.
4. Optionally set `inventory_text` and `inventory_region` if you want an extra gate.
5. Restart the app.

## Output files

- `data/items.json`: canonical item entries
- `data/index.json`: alias map for OCR matching
- `data/meta.json`: revision map + sync metadata
