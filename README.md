# Miners Haven Hover Viewer + Optimizer

Desktop companion for Miners Haven with two tools: an OCR hover viewer for inventory items, and a layout optimizer planner that estimates money/sec and time-to-target from your inventory.

## Features

- Full wiki sync for `Category:Upgrader`, `Category:Furnace`, `Category:Dropper`, and `Category:Mine`
- Permanent JSON cache (`data/items.json`, `data/index.json`, `data/meta.json`)
- Incremental refresh using wiki revision IDs
- Hold-key trigger (`left alt` by default)
- Inventory-mode gate via OCR keyword detection (optional, disabled by default)
- Top-right viewer with core info plus a `More Details` section
- Optimizer planner with legality-aware chain simulation (resetters, teleporters, split/merge, destroy effects)

## Captured data

- Core: name, type, tier, description, how-to-use, multiplier, size, MPU, drop rate, ore worth, wiki URL
- Extended: extracted `effects` profile and `synergies` links/keywords for planning algorithms
- Details:
  - elements (raw and named)
  - proof and limits (reborn proof, sacrifice proof, upgrade limit/counter, can reset)
  - acquisition info
  - effect tags
  - parsed effects profile (status effects, mechanics, x-values)
  - parsed synergies (related items + synergy keywords)
  - rules-driven extraction powered by `mh_viewer/effect_rules.json`
  - ore worth normalization (`kind=static|range|dynamic`, confidence, parsed values)
  - throughput profile (`ores_per_second`, value/sec estimates, throughput multipliers)
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

## Setup Optimizer

### 1. Sync wiki cache

```bash
python -m mh_viewer.app --sync-only --full
```

This downloads all upgraders, furnaces, droppers, and mines with parsed effects, synergies, drop rates, ore worth, and throughput profiles.

### 2. Launch planner calculator UI

```bash
python -m mh_viewer.app --planner
```

### 3. Paste your inventory

Paste items into the left text box. Supported formats:

- `King Gold Mine x1`
- `Ore Illuminator x5`
- `3x Newtonium Mine`

You can also skip manual paste and use the built-in add flow:

- Type in **Search item** to filter the local item catalog
- Select a suggestion, choose quantity, then click **Add Selected**
- Or click **Add Typed** to add exactly what you wrote
- Adding an existing item now increments that item's quantity instead of creating duplicate lines
- **Remove Last** and **Clear** help manage the list quickly

### 4. Choose mode and calculate

- **Money/sec**: estimate fastest steady income
- **Time-to-target**: estimate ETA from 0 cash to targets like `7 de`

Adjust controls:

- `Max mines`: auto-select top 1-3 mines from your inventory
- `Loop cap`: resetter loop passes used by the calculator (default 4)
- `No destroy items`: excludes ore-destroying items from chain selection

Click **Calculate** to see selected mines, upgrader chain, bottleneck diagnostics, and ETA.

Planner session persistence:

- The planner now auto-saves your inventory input + mode/settings to `data/planner_state.json`
- State is restored automatically the next time you open `--planner`

Current calculator simulation includes:

- ordered pipeline phases (`pre`, `core`, `post`, `output`)
- legality-aware chain filtering (resetter/teleporter/split/merge constraints)
- per-phase throughput floors + topology throughput taxes
- loop-pass simulation with diminishing returns and MPU caps
- explicit limiter diagnostics (MPU-constrained vs conveyor-constrained)
- dimension pressure penalty based on tile usage against base limit (default 62x62 -> 3844 tiles)
- automatic limiter recommendation (which improvement gives best next gain)
- cell furnace guardrail: cell furnaces are excluded from upgrader chains (treated as raw-ore-only outputs)
- early synergy scoring from parsed effects/synergies (works-with, related-item hits, status stacking/conflict penalties)

## Run hover viewer

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
