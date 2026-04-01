from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class CacheSnapshot:
    items: dict[str, dict[str, Any]]
    index: dict[str, Any]
    meta: dict[str, Any]


class CacheStore:
    def __init__(self, root_dir: str | Path) -> None:
        self.root_dir = Path(root_dir)
        self.items_path = self.root_dir / "items.json"
        self.index_path = self.root_dir / "index.json"
        self.meta_path = self.root_dir / "meta.json"

    def ensure(self) -> None:
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def load(self) -> CacheSnapshot:
        self.ensure()
        items_payload = _read_json(self.items_path, {"items": {}})
        index_payload = _read_json(self.index_path, {"aliases": {}, "names": {}})
        meta_payload = _read_json(
            self.meta_path,
            {
                "revision_map": {},
                "categories": ["Category:Upgrader", "Category:Furnace"],
            },
        )

        items_raw = items_payload.get("items", {})
        items: dict[str, dict[str, Any]] = {}
        if isinstance(items_raw, dict):
            for key, value in items_raw.items():
                if isinstance(key, str) and isinstance(value, dict):
                    items[key] = value
        return CacheSnapshot(items=items, index=index_payload, meta=meta_payload)

    def save(self, items: dict[str, dict[str, Any]], index: dict[str, Any], meta: dict[str, Any]) -> None:
        self.ensure()
        now = datetime.now(tz=timezone.utc).isoformat()
        items_payload = {
            "generated_at": now,
            "count": len(items),
            "items": items,
        }
        index_payload = {
            "generated_at": now,
            "aliases": index.get("aliases", {}),
            "names": index.get("names", {}),
        }
        meta_payload = dict(meta)
        meta_payload["updated_at"] = now

        self.items_path.write_text(json.dumps(items_payload, indent=2, sort_keys=True), encoding="utf-8")
        self.index_path.write_text(json.dumps(index_payload, indent=2, sort_keys=True), encoding="utf-8")
        self.meta_path.write_text(json.dumps(meta_payload, indent=2, sort_keys=True), encoding="utf-8")


def build_index(items: dict[str, dict[str, Any]]) -> dict[str, Any]:
    alias_map: dict[str, list[str]] = {}
    names: dict[str, str] = {}
    for normalized_name, item in items.items():
        names[normalized_name] = str(item.get("name", normalized_name))
        aliases = item.get("aliases") or []
        if isinstance(aliases, list):
            for alias in aliases:
                if not isinstance(alias, str) or not alias:
                    continue
                alias_map.setdefault(alias, [])
                if normalized_name not in alias_map[alias]:
                    alias_map[alias].append(normalized_name)
        alias_map.setdefault(normalized_name, [])
        if normalized_name not in alias_map[normalized_name]:
            alias_map[normalized_name].append(normalized_name)

    return {
        "aliases": alias_map,
        "names": names,
    }


def _read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return dict(default)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(default)
    return payload if isinstance(payload, dict) else dict(default)
