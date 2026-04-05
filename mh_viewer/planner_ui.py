from __future__ import annotations

import re
import tkinter as tk
from urllib.parse import quote, urlparse
import webbrowser
from tkinter import ttk
from typing import Any

from .calculator import CalculationResult, UNIT_ORDER, format_cash_amount, format_duration
from .utils import normalize_lookup


_CATALOG_TIER_ORDER = {
    "limited_reborn": 0,
    "eggxotic": 1,
    "contraband": 2,
    "luxury": 3,
    "exotic": 4,
    "advanced_reborn": 5,
    "reborn": 6,
    "collectible": 7,
    "premium": 8,
    "other": 9,
}
_TARGET_SUFFIXES = ["", *[unit for unit in UNIT_ORDER if unit]]


def _tier_bucket_rank(bucket: str) -> int:
    return _CATALOG_TIER_ORDER.get(str(bucket or "other"), _CATALOG_TIER_ORDER["other"])


class PlannerWindow:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("MH Optimizer Planner")
        self.root.configure(bg="#0E121A")
        self.root.geometry(self._compute_geometry(1240, 740))
        self.root.minsize(1040, 640)
        self._close_callback: Any | None = None
        self._change_callback: Any | None = None
        self._suspend_change_notifications = False

        self._catalog_entries: list[dict[str, Any]] = []
        self._catalog_names: list[str] = []
        self._catalog_tags: list[str] = []
        self._catalog_sort_index: dict[str, tuple[int, bool, float, str]] = {}

        self._owned_item_rows: dict[str, dict[str, Any]] = {}
        self._owned_items_all: list[dict[str, Any]] = []
        self._owned_unknown_count = 0
        self._owned_sort_column: str | None = None
        self._owned_sort_desc = True
        self._owned_tier_filter: str | None = None
        self._owned_type_filter: str | None = None
        self._selected_wiki_url = ""

        self._build_layout()
        self.root.protocol("WM_DELETE_WINDOW", self._on_window_close)

    def _build_layout(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("Card.TFrame", background="#151B26")
        style.configure("Header.TLabel", background="#151B26", foreground="#F3F6FC", font=("Segoe UI", 16, "bold"))
        style.configure("Sub.TLabel", background="#151B26", foreground="#9CA8BD", font=("Segoe UI", 10))
        style.configure("Body.TLabel", background="#151B26", foreground="#DFE5F2", font=("Segoe UI", 10))

        outer = ttk.Frame(self.root, style="Card.TFrame", padding=14)
        outer.pack(fill=tk.BOTH, expand=True)

        left = ttk.Frame(outer, style="Card.TFrame")
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        right = ttk.Frame(outer, style="Card.TFrame")
        right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(14, 0))

        ttk.Label(left, text="Inventory Input", style="Header.TLabel").pack(anchor="w")
        ttk.Label(left, text="Paste items like: King Gold Mine x1, Ore Illuminator x3", style="Sub.TLabel").pack(anchor="w", pady=(0, 8))

        add_frame = ttk.Frame(left, style="Card.TFrame")
        add_frame.pack(fill=tk.X, pady=(0, 8))

        ttk.Label(add_frame, text="Search item", style="Body.TLabel").grid(row=0, column=0, sticky="w")
        self.search_var = tk.StringVar(value="")
        self.search_entry = ttk.Entry(add_frame, textvariable=self.search_var, width=36)
        self.search_entry.grid(row=0, column=1, sticky="w", padx=(8, 0))

        self.catalog_mode_var = tk.StringVar(value="all")
        ttk.Radiobutton(add_frame, text="Show all", variable=self.catalog_mode_var, value="all").grid(
            row=1,
            column=1,
            sticky="w",
            pady=(6, 0),
        )
        ttk.Radiobutton(add_frame, text="Show by tag", variable=self.catalog_mode_var, value="tag").grid(
            row=1,
            column=2,
            sticky="w",
            padx=(10, 0),
            pady=(6, 0),
        )
        self.catalog_tag_var = tk.StringVar(value="All tags")
        self.catalog_tag_combo = ttk.Combobox(
            add_frame,
            textvariable=self.catalog_tag_var,
            values=["All tags"],
            state="readonly",
            width=24,
        )
        self.catalog_tag_combo.grid(row=1, column=3, columnspan=2, sticky="w", padx=(8, 0), pady=(6, 0))
        self.catalog_mode_var.trace_add("write", self._on_catalog_filter_changed)
        self.catalog_tag_var.trace_add("write", self._on_catalog_filter_changed)
        self._sync_catalog_filter_controls()

        ttk.Label(add_frame, text="Qty", style="Body.TLabel").grid(row=0, column=2, sticky="w", padx=(10, 0))
        self.add_qty_var = tk.IntVar(value=1)
        self.add_qty_spin = ttk.Spinbox(add_frame, from_=1, to=999, textvariable=self.add_qty_var, width=6)
        self.add_qty_spin.grid(row=0, column=3, sticky="w", padx=(6, 0))

        self.add_selected_button = ttk.Button(add_frame, text="Add Selected", command=self._add_selected_item)
        self.add_selected_button.grid(row=0, column=4, sticky="w", padx=(10, 0))

        self.add_typed_button = ttk.Button(add_frame, text="Add Typed", command=self._add_typed_item)
        self.add_typed_button.grid(row=0, column=5, sticky="w", padx=(8, 0))

        self.remove_last_button = ttk.Button(add_frame, text="Remove Last", command=self._remove_last_item)
        self.remove_last_button.grid(row=0, column=6, sticky="w", padx=(8, 0))

        self.clear_button = ttk.Button(add_frame, text="Clear", command=self._clear_inventory_input)
        self.clear_button.grid(row=0, column=7, sticky="w", padx=(8, 0))

        self.sort_button = ttk.Button(add_frame, text="Sort List", command=self.sort_inventory_lines)
        self.sort_button.grid(row=0, column=8, sticky="w", padx=(8, 0))

        suggest_frame = ttk.Frame(left, style="Card.TFrame")
        suggest_frame.pack(fill=tk.X, pady=(0, 8))
        self.suggestion_list = tk.Listbox(
            suggest_frame,
            height=6,
            bg="#0E121A",
            fg="#DCE6F8",
            selectbackground="#263249",
            activestyle="none",
        )
        self.suggestion_list.pack(side=tk.LEFT, fill=tk.X, expand=True)
        scrollbar = ttk.Scrollbar(suggest_frame, orient=tk.VERTICAL, command=self.suggestion_list.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.suggestion_list.configure(yscrollcommand=scrollbar.set)

        self.search_var.trace_add("write", self._on_search_changed)
        self.search_entry.bind("<Return>", lambda _event: self._add_selected_item())
        self.suggestion_list.bind("<Double-Button-1>", lambda _event: self._add_selected_item())

        self.inventory_text = tk.Text(left, height=16, bg="#0E121A", fg="#DCE6F8", insertbackground="#DCE6F8", wrap=tk.WORD)
        self.inventory_text.pack(fill=tk.BOTH, expand=True)
        self.inventory_text.bind("<<Modified>>", self._on_inventory_modified)

        controls = ttk.Frame(left, style="Card.TFrame")
        controls.pack(fill=tk.X, pady=(10, 0))

        self.mode_var = tk.StringVar(value="money_per_sec")
        ttk.Radiobutton(controls, text="Money/sec", variable=self.mode_var, value="money_per_sec").grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(controls, text="Time-to-target", variable=self.mode_var, value="time_to_target").grid(row=0, column=1, sticky="w", padx=(12, 0))

        self.no_destroy_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(controls, text="No destroy items", variable=self.no_destroy_var).grid(
            row=0,
            column=2,
            sticky="w",
            padx=(16, 0),
        )

        self.target_number_var = tk.StringVar(value="7")
        self.target_number_var.trace_add("write", self._on_state_control_changed)
        self.target_suffix_var = tk.StringVar(value="de")
        self.target_suffix_var.trace_add("write", self._on_state_control_changed)
        self.max_mines_var = tk.IntVar(value=3)
        self.max_mines_var.trace_add("write", self._on_state_control_changed)
        self.max_upgraders_var = tk.IntVar(value=20)
        self.max_upgraders_var.trace_add("write", self._on_state_control_changed)
        self.mode_var.trace_add("write", self._on_state_control_changed)
        self.no_destroy_var.trace_add("write", self._on_state_control_changed)

        ttk.Label(controls, text="Target", style="Body.TLabel").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(controls, textvariable=self.target_number_var, width=10).grid(row=1, column=1, sticky="w", pady=(8, 0))
        self.target_suffix_combo = ttk.Combobox(
            controls,
            textvariable=self.target_suffix_var,
            values=_TARGET_SUFFIXES,
            state="readonly",
            width=6,
        )
        self.target_suffix_combo.grid(row=1, column=2, sticky="w", pady=(8, 0), padx=(6, 0))

        ttk.Label(controls, text="Max mines", style="Body.TLabel").grid(row=1, column=3, sticky="w", pady=(8, 0), padx=(12, 0))
        ttk.Spinbox(controls, from_=1, to=3, textvariable=self.max_mines_var, width=5).grid(row=1, column=4, sticky="w", pady=(8, 0))

        ttk.Label(controls, text="Total item count", style="Body.TLabel").grid(row=1, column=5, sticky="w", pady=(8, 0), padx=(12, 0))
        ttk.Spinbox(controls, from_=1, to=60, textvariable=self.max_upgraders_var, width=5).grid(row=1, column=6, sticky="w", pady=(8, 0))

        self.run_button = ttk.Button(controls, text="Calculate")
        self.run_button.grid(row=2, column=0, sticky="w", pady=(10, 0))

        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(controls, textvariable=self.status_var, style="Sub.TLabel").grid(row=2, column=1, columnspan=6, sticky="w", pady=(10, 0), padx=(8, 0))

        style.configure(
            "Planner.Treeview",
            background="#0E121A",
            foreground="#DCE6F8",
            fieldbackground="#0E121A",
            rowheight=22,
        )
        style.map(
            "Planner.Treeview",
            background=[("selected", "#2A3B56")],
            foreground=[("selected", "#F3F8FF")],
        )
        style.configure("Planner.Treeview.Heading", background="#1C2638", foreground="#E7EDF8", font=("Segoe UI", 10, "bold"))
        style.configure("Link.TLabel", background="#151B26", foreground="#79B2FF", font=("Segoe UI", 10, "underline"))

        self.right_notebook = ttk.Notebook(right)
        self.right_notebook.pack(fill=tk.BOTH, expand=True)

        estimate_tab = ttk.Frame(self.right_notebook, style="Card.TFrame", padding=(0, 4, 0, 0))
        self.right_notebook.add(estimate_tab, text="Estimate")

        ttk.Label(estimate_tab, text="Estimate", style="Header.TLabel").pack(anchor="w")
        self.summary_var = tk.StringVar(value="Run calculation to see output")
        ttk.Label(estimate_tab, textvariable=self.summary_var, style="Body.TLabel", wraplength=540, justify=tk.LEFT).pack(anchor="w", pady=(0, 8))

        self.output_text = tk.Text(
            estimate_tab,
            height=24,
            bg="#0E121A",
            fg="#CDE0FF",
            relief=tk.FLAT,
            font=("Consolas", 10),
            wrap=tk.WORD,
        )
        self.output_text.pack(fill=tk.BOTH, expand=True)
        self.output_text.configure(state=tk.DISABLED)

        items_tab = ttk.Frame(self.right_notebook, style="Card.TFrame", padding=(0, 4, 0, 0))
        self.right_notebook.add(items_tab, text="My Items")

        ttk.Label(items_tab, text="My Items", style="Header.TLabel").pack(anchor="w")
        self.owned_hint_var = tk.StringVar(value="Add items on the left to populate your inventory list.")
        ttk.Label(items_tab, textvariable=self.owned_hint_var, style="Sub.TLabel").pack(anchor="w", pady=(0, 8))

        owned_search_frame = ttk.Frame(items_tab, style="Card.TFrame")
        owned_search_frame.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(owned_search_frame, text="Search my items", style="Body.TLabel").pack(side=tk.LEFT)
        self.owned_search_var = tk.StringVar(value="")
        self.owned_search_var.trace_add("write", self._on_owned_search_changed)
        self.owned_search_entry = ttk.Entry(owned_search_frame, textvariable=self.owned_search_var, width=38)
        self.owned_search_entry.pack(side=tk.LEFT, padx=(8, 0))

        items_split = ttk.Panedwindow(items_tab, orient=tk.VERTICAL)
        items_split.pack(fill=tk.BOTH, expand=True)

        table_frame = ttk.Frame(items_split, style="Card.TFrame")
        details_frame = ttk.Frame(items_split, style="Card.TFrame")
        items_split.add(table_frame, weight=3)
        items_split.add(details_frame, weight=2)

        self.owned_tree = ttk.Treeview(
            table_frame,
            columns=("name", "qty", "mpu", "mult", "tier", "type"),
            show="headings",
            style="Planner.Treeview",
            selectmode="browse",
        )
        self.owned_tree.heading("name", text="Name")
        self.owned_tree.heading("qty", text="Qty", command=lambda: self._on_owned_sort_heading("qty"))
        self.owned_tree.heading("mpu", text="MPU", command=lambda: self._on_owned_sort_heading("mpu"))
        self.owned_tree.heading("mult", text="Multiplier", command=lambda: self._on_owned_sort_heading("mult"))
        self.owned_tree.heading("tier", text="Tier", command=lambda: self._open_owned_filter_menu("tier"))
        self.owned_tree.heading("type", text="Type", command=lambda: self._open_owned_filter_menu("type"))

        self.owned_tree.column("name", width=225, anchor=tk.W)
        self.owned_tree.column("qty", width=55, anchor=tk.CENTER)
        self.owned_tree.column("mpu", width=85, anchor=tk.CENTER)
        self.owned_tree.column("mult", width=90, anchor=tk.CENTER)
        self.owned_tree.column("tier", width=115, anchor=tk.W)
        self.owned_tree.column("type", width=90, anchor=tk.W)

        self.owned_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        owned_scroll = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.owned_tree.yview)
        owned_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.owned_tree.configure(yscrollcommand=owned_scroll.set)

        self.owned_tree.bind("<<TreeviewSelect>>", self._on_owned_tree_selected)
        self.owned_tree.bind("<Button-1>", self._on_owned_tree_click, add="+")
        self.owned_tree.bind("<Double-1>", self._on_owned_tree_activate)
        self.owned_tree.bind("<Motion>", self._on_owned_tree_motion)

        self.owned_title_var = tk.StringVar(value="Select an item")
        ttk.Label(details_frame, textvariable=self.owned_title_var, style="Body.TLabel").pack(anchor="w")

        self.owned_wiki_var = tk.StringVar(value="Wiki: -")
        self.owned_wiki_label = ttk.Label(details_frame, textvariable=self.owned_wiki_var, style="Sub.TLabel")
        self.owned_wiki_label.pack(anchor="w", pady=(4, 6))
        self.owned_wiki_label.bind("<Button-1>", self._open_selected_wiki)

        self.owned_details_text = tk.Text(
            details_frame,
            height=10,
            bg="#0E121A",
            fg="#CDE0FF",
            relief=tk.FLAT,
            font=("Consolas", 10),
            wrap=tk.WORD,
        )
        self.owned_details_text.pack(fill=tk.BOTH, expand=True)
        self.owned_details_text.configure(state=tk.DISABLED)

    def set_run_callback(self, callback: Any) -> None:
        self.run_button.configure(command=callback)

    def set_close_callback(self, callback: Any) -> None:
        self._close_callback = callback

    def set_change_callback(self, callback: Any) -> None:
        self._change_callback = callback

    def set_item_catalog(self, entries: list[dict[str, Any]]) -> None:
        unique_names: set[str] = set()
        normalized_entries: list[dict[str, Any]] = []
        all_tags: set[str] = set()

        for raw in entries:
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("name") or "").strip()
            if not name or name in unique_names:
                continue
            unique_names.add(name)

            normalized_name = normalize_lookup(str(raw.get("normalized_name") or name))
            tier_bucket = str(raw.get("tier_bucket") or "other")
            tier_raw = str(raw.get("tier") or "")

            tags_raw = raw.get("tags")
            tags: list[str] = []
            if isinstance(tags_raw, list):
                for token in tags_raw:
                    text = str(token).strip()
                    if text:
                        tags.append(text)
                        all_tags.add(text)

            normalized_entries.append(
                {
                    "name": name,
                    "normalized": normalize_lookup(name),
                    "normalized_name": normalized_name,
                    "tier_bucket": tier_bucket,
                    "tier_rank": _tier_bucket_rank(tier_bucket),
                    "rarity": raw.get("rarity"),
                    "rarity_sort": self._as_float(raw.get("rarity_sort")),
                    "tier": tier_raw,
                    "tags": sorted(set(tags), key=str.lower),
                    "limited": bool(raw.get("limited")),
                }
            )

        normalized_entries.sort(
            key=lambda entry: (
                int(entry.get("tier_rank") or 99),
                entry.get("rarity_sort") is None,
                entry.get("rarity_sort") if entry.get("rarity_sort") is not None else float("inf"),
                str(entry.get("name") or "").lower(),
            )
        )
        self._catalog_entries = normalized_entries
        self._catalog_names = [str(entry.get("name") or "") for entry in normalized_entries]
        self._catalog_tags = sorted(all_tags, key=str.lower)
        self._catalog_sort_index = {}
        for entry in normalized_entries:
            rank = int(entry.get("tier_rank") or 99)
            rarity_sort = self._as_float(entry.get("rarity_sort"))
            rarity_none = rarity_sort is None
            rarity_value = rarity_sort if rarity_sort is not None else float("inf")
            label = str(entry.get("name") or "").lower()
            for key in [str(entry.get("normalized") or ""), str(entry.get("normalized_name") or "")]:
                if not key:
                    continue
                current = self._catalog_sort_index.get(key)
                candidate = (rank, rarity_none, rarity_value, label)
                if current is None or candidate < current:
                    self._catalog_sort_index[key] = candidate
        self.catalog_tag_combo.configure(values=["All tags", *self._catalog_tags])
        if self.catalog_tag_var.get() not in ["All tags", *self._catalog_tags]:
            self.catalog_tag_var.set("All tags")
        self._refresh_suggestions(self.search_var.get().strip())

    def _compose_target_text(self) -> str:
        number = self.target_number_var.get().strip()
        suffix = self.target_suffix_var.get().strip().lower()
        if not number:
            number = "0"
        if suffix and suffix in _TARGET_SUFFIXES:
            return f"{number} {suffix}"
        return number

    def _split_target_text(self, text: str) -> tuple[str, str]:
        cleaned = str(text).strip().lower().replace(",", " ")
        match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*([a-z]{0,4})", cleaned)
        if not match:
            return ("", "de")
        number = str(match.group(1)).strip()
        suffix = str(match.group(2)).strip()
        if suffix == "n":
            suffix = "no"
        if suffix not in _TARGET_SUFFIXES:
            suffix = "de"
        return (number, suffix)

    def get_inventory_text(self) -> str:
        return self.inventory_text.get("1.0", tk.END).strip()

    def get_inputs(self) -> tuple[str, str, int, bool, int, bool]:
        inventory = self.get_inventory_text()
        target_text = self._compose_target_text()
        max_mines = max(1, min(3, int(self.max_mines_var.get() or 3)))
        use_target = self.mode_var.get() == "time_to_target"
        max_upgraders = max(1, min(60, int(self.max_upgraders_var.get() or 20)))
        ban_destroy = bool(self.no_destroy_var.get())
        return (inventory, target_text, max_mines, use_target, max_upgraders, ban_destroy)

    def set_owned_items(self, entries: list[dict[str, Any]], unknown_entries: list[str] | None = None) -> None:
        rows = entries if isinstance(entries, list) else []
        unknown = unknown_entries if isinstance(unknown_entries, list) else []

        self._owned_items_all = [entry for entry in rows if isinstance(entry, dict)]
        self._owned_unknown_count = len(unknown)
        self._refresh_owned_items_view()

    def _refresh_owned_items_view(self) -> None:
        query_norm = normalize_lookup(self.owned_search_var.get().strip())

        if not query_norm:
            rows = list(self._owned_items_all)
        else:
            rows = []
            for entry in self._owned_items_all:
                name = str(entry.get("name") or "")
                tier = str(entry.get("tier") or "")
                item_type = str(entry.get("item_type") or "")
                portable_text = "portable" if bool(entry.get("is_portable")) else ""
                haystack = " ".join([name, tier, item_type, portable_text])
                if query_norm in normalize_lookup(haystack):
                    rows.append(entry)

        if self._owned_tier_filter:
            rows = [entry for entry in rows if str(entry.get("tier") or "") == self._owned_tier_filter]
        if self._owned_type_filter:
            if self._owned_type_filter == "Portable":
                rows = [entry for entry in rows if bool(entry.get("is_portable"))]
            else:
                rows = [entry for entry in rows if str(entry.get("item_type") or "") == self._owned_type_filter]

        rows = self._apply_owned_sort(rows)

        children = self.owned_tree.get_children()
        if children:
            self.owned_tree.delete(*children)
        self._owned_item_rows = {}

        unique_count = 0
        total_quantity = 0
        for idx, entry in enumerate(rows):
            if not isinstance(entry, dict):
                continue
            unique_count += 1
            quantity = max(0, int(entry.get("quantity") or 0))
            total_quantity += quantity

            item_id = f"owned_{idx}"
            self._owned_item_rows[item_id] = entry
            self.owned_tree.insert(
                "",
                tk.END,
                iid=item_id,
                values=(
                    str(entry.get("name") or "-"),
                    quantity,
                    str(entry.get("mpu_text") or "-"),
                    str(entry.get("multiplier_text") or "-"),
                    str(entry.get("tier") or "-"),
                    str(entry.get("item_type") or "-"),
                ),
            )

        total_unique = len(self._owned_items_all)
        total_qty_all = sum(max(0, int(entry.get("quantity") or 0)) for entry in self._owned_items_all)
        if total_unique == 0:
            hint = "No matched items in your inventory text yet."
        else:
            hint = (
                f"Showing {unique_count}/{total_unique} unique item(s), "
                f"{total_quantity}/{total_qty_all} copies. "
                "Sorted by tier group, then lower rarity first."
            )
        filters: list[str] = []
        if self._owned_tier_filter:
            filters.append(f"Tier={self._owned_tier_filter}")
        if self._owned_type_filter:
            filters.append(f"Type={self._owned_type_filter}")
        if filters:
            hint = f"{hint} Filter: {', '.join(filters)}."
        if self._owned_unknown_count > 0:
            hint = f"{hint} Unmatched entries: {self._owned_unknown_count}."
        self.owned_hint_var.set(hint)

        children = self.owned_tree.get_children()
        if children:
            first = str(children[0])
            self.owned_tree.selection_set(first)
            self.owned_tree.focus(first)
            self.owned_tree.see(first)
            self._show_owned_item_details(first)
        else:
            self._show_owned_item_details(None)

    def _on_owned_search_changed(self, *_args: Any) -> None:
        self._refresh_owned_items_view()

    def _on_owned_sort_heading(self, column: str) -> None:
        if column not in {"qty", "mpu", "mult"}:
            return

        if self._owned_sort_column == column:
            self._owned_sort_desc = not self._owned_sort_desc
        else:
            self._owned_sort_column = column
            self._owned_sort_desc = True

        self._refresh_owned_heading_labels()
        self._refresh_owned_items_view()

    def _refresh_owned_heading_labels(self) -> None:
        qty_label = "Qty"
        mpu_label = "MPU"
        mult_label = "Multiplier"
        tier_label = "Tier"
        type_label = "Type"
        if self._owned_sort_column == "qty":
            qty_label = "Qty v" if self._owned_sort_desc else "Qty ^"
        elif self._owned_sort_column == "mpu":
            mpu_label = "MPU v" if self._owned_sort_desc else "MPU ^"
        elif self._owned_sort_column == "mult":
            mult_label = "Multiplier v" if self._owned_sort_desc else "Multiplier ^"

        if self._owned_tier_filter:
            tier_label = f"Tier [{self._owned_tier_filter}]"
        if self._owned_type_filter:
            type_label = f"Type [{self._owned_type_filter}]"

        self.owned_tree.heading("qty", text=qty_label)
        self.owned_tree.heading("mpu", text=mpu_label)
        self.owned_tree.heading("mult", text=mult_label)
        self.owned_tree.heading("tier", text=tier_label)
        self.owned_tree.heading("type", text=type_label)

    def _apply_owned_sort(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if self._owned_sort_column == "qty":
            return sorted(
                rows,
                key=lambda entry: (
                    max(0, int(entry.get("quantity") or 0)),
                    str(entry.get("name") or "").lower(),
                ),
                reverse=self._owned_sort_desc,
            )

        if self._owned_sort_column == "mpu":
            if self._owned_sort_desc:
                return sorted(
                    rows,
                    key=lambda entry: (
                        self._as_float(entry.get("mpu_value")) is None,
                        -(self._as_float(entry.get("mpu_value")) or 0.0),
                        str(entry.get("name") or "").lower(),
                    ),
                )
            return sorted(
                rows,
                key=lambda entry: (
                    self._as_float(entry.get("mpu_value")) is None,
                    self._as_float(entry.get("mpu_value")) or 0.0,
                    str(entry.get("name") or "").lower(),
                ),
            )

        if self._owned_sort_column == "mult":
            if self._owned_sort_desc:
                return sorted(
                    rows,
                    key=lambda entry: (
                        self._as_float(entry.get("multiplier_value")) is None,
                        -(self._as_float(entry.get("multiplier_value")) or 0.0),
                        str(entry.get("name") or "").lower(),
                    ),
                )
            return sorted(
                rows,
                key=lambda entry: (
                    self._as_float(entry.get("multiplier_value")) is None,
                    self._as_float(entry.get("multiplier_value")) or 0.0,
                    str(entry.get("name") or "").lower(),
                ),
            )

        return rows

    def _open_owned_filter_menu(self, column: str) -> None:
        if column not in {"tier", "type"}:
            return

        menu = tk.Menu(self.root, tearoff=0)
        if column == "tier":
            menu.add_command(label="All tiers", command=lambda: self._set_owned_filter("tier", None))
            for value in self._owned_tier_values():
                menu.add_command(label=value, command=lambda selected=value: self._set_owned_filter("tier", selected))
        else:
            menu.add_command(label="All types", command=lambda: self._set_owned_filter("type", None))
            for value in self._owned_type_values():
                menu.add_command(label=value, command=lambda selected=value: self._set_owned_filter("type", selected))

        try:
            menu.tk_popup(self.root.winfo_pointerx(), self.root.winfo_pointery())
        finally:
            menu.grab_release()

    def _set_owned_filter(self, column: str, value: str | None) -> None:
        if column == "tier":
            self._owned_tier_filter = value
        elif column == "type":
            self._owned_type_filter = value
        else:
            return
        self._refresh_owned_heading_labels()
        self._refresh_owned_items_view()

    def _owned_tier_values(self) -> list[str]:
        pairs: list[tuple[int, str]] = []
        seen: set[str] = set()
        for entry in self._owned_items_all:
            tier = str(entry.get("tier") or "").strip()
            if not tier or tier in seen:
                continue
            seen.add(tier)
            bucket = str(entry.get("tier_bucket") or "other")
            pairs.append((_tier_bucket_rank(bucket), tier))
        pairs.sort(key=lambda pair: (pair[0], pair[1].lower()))
        return [pair[1] for pair in pairs]

    def _owned_type_values(self) -> list[str]:
        values = sorted(
            {
                str(entry.get("item_type") or "").strip()
                for entry in self._owned_items_all
                if str(entry.get("item_type") or "").strip()
            },
            key=str.lower,
        )
        if any(bool(entry.get("is_portable")) for entry in self._owned_items_all):
            values = ["Portable", *[value for value in values if value.lower() != "portable"]]
        return values

    def get_state(self) -> dict[str, Any]:
        inventory, target_text, max_mines, use_target, max_upgraders, ban_destroy = self.get_inputs()
        return {
            "inventory_text": inventory,
            "target_text": target_text,
            "target_number": self.target_number_var.get().strip(),
            "target_suffix": self.target_suffix_var.get().strip(),
            "max_mines": max_mines,
            "use_target_mode": use_target,
            "max_upgraders": max_upgraders,
            "ban_destroy": ban_destroy,
            "catalog_show_mode": self.catalog_mode_var.get(),
            "catalog_tag": self.catalog_tag_var.get(),
        }

    def apply_state(self, state: dict[str, Any]) -> None:
        if not isinstance(state, dict):
            return

        self._suspend_change_notifications = True
        try:
            inventory_text = str(state.get("inventory_text") or "").strip()
            self.inventory_text.delete("1.0", tk.END)
            if inventory_text:
                self.inventory_text.insert("1.0", inventory_text)

            target_number = str(state.get("target_number") or "").strip()
            target_suffix = str(state.get("target_suffix") or "").strip().lower()
            if not target_number:
                parsed_number, parsed_suffix = self._split_target_text(str(state.get("target_text") or "7 de"))
                target_number = parsed_number
                target_suffix = parsed_suffix
            if not target_number:
                target_number = "7"
            if target_suffix not in _TARGET_SUFFIXES:
                target_suffix = "de"
            self.target_number_var.set(target_number)
            self.target_suffix_var.set(target_suffix)

            max_mines = state.get("max_mines", 3)
            try:
                self.max_mines_var.set(max(1, min(3, int(max_mines))))
            except Exception:
                self.max_mines_var.set(3)

            max_upgraders = state.get("max_upgraders", state.get("loop_cap", 20))
            try:
                self.max_upgraders_var.set(max(1, min(60, int(max_upgraders))))
            except Exception:
                self.max_upgraders_var.set(20)

            use_target_mode = bool(state.get("use_target_mode", False))
            self.mode_var.set("time_to_target" if use_target_mode else "money_per_sec")

            self.no_destroy_var.set(bool(state.get("ban_destroy", False)))

            show_mode = str(state.get("catalog_show_mode") or "all")
            self.catalog_mode_var.set("tag" if show_mode == "tag" else "all")
            saved_tag = str(state.get("catalog_tag") or "All tags")
            if saved_tag and (saved_tag == "All tags" or saved_tag in self._catalog_tags):
                self.catalog_tag_var.set(saved_tag)
            else:
                self.catalog_tag_var.set("All tags")
        finally:
            self._suspend_change_notifications = False
            self.inventory_text.edit_modified(False)

    def show_result(self, result: CalculationResult) -> None:
        lines: list[str] = []
        lines.append("Selected Mines")
        for idx, pick in enumerate(result.mine_picks, start=1):
            ore_rate = "?" if pick.ore_per_second is None else f"{pick.ore_per_second:.4f}/s"
            lines.append(
                f"{idx}. {pick.name} | ore/s={ore_rate} | v/s={format_cash_amount(pick.expected_value_per_second)} | data_conf={pick.confidence:.2f}"
            )

        if not result.mine_picks:
            lines.append("- none")

        lines.append("")
        lines.append("Selected Upgraders")
        for idx, pick in enumerate(result.upgrader_picks[:12], start=1):
            flags = []
            if pick.is_resetter:
                flags.append("reset")
            if pick.destroys_ore:
                flags.append("destroy")
            if pick.is_furnace:
                flags.append("furnace")
            if pick.is_portable:
                flags.append("portable")
            if pick.is_blaster:
                flags.append("blaster")
            if pick.conditional_fire_multiplier is not None:
                flags.append(f"fire->{pick.conditional_fire_multiplier:g}")
            if pick.overheated_multiplier is not None:
                flags.append(f"overheat->{pick.overheated_multiplier:g}")
            if pick.extinguishes_fire:
                flags.append("extinguish")
            if pick.first_only:
                flags.append("first-only")
            if pick.max_uses is not None:
                flags.append(f"max{pick.max_uses}")
            flag_text = f" [{','.join(flags)}]" if flags else ""
            lines.append(
                f"{idx}. {pick.name} | x{pick.multiplier:g} | data_conf={pick.confidence:.2f}{flag_text}"
            )
        if not result.upgrader_picks:
            lines.append("- none")

        lines.append("")
        lines.append("Pipeline Phases")
        for phase_name in ["pre", "core", "post", "output"]:
            names = result.phase_breakdown.get(phase_name, [])
            if names:
                preview = ", ".join(names[:4])
                suffix = " ..." if len(names) > 4 else ""
                lines.append(f"{phase_name}: {len(names)} | {preview}{suffix}")
            else:
                lines.append(f"{phase_name}: 0")

        lines.append("")
        lines.append("Aggregate")
        lines.append(f"Mine value/sec: {format_cash_amount(result.mine_expected_value_per_second)}")
        lines.append(f"Base ore rate: {result.base_ores_per_second:.4f}/s")
        lines.append(f"Effective ore rate: {result.effective_ores_per_second:.4f}/s")
        lines.append(f"Bottleneck multiplier: x{result.bottleneck_multiplier:.3g}")
        lines.append(f"Chain multiplier estimate: x{result.estimated_multiplier:.3g}")
        lines.append(f"Loop passes used: {result.loop_passes}")
        if result.mpu_constrained and result.mpu_effective_cap is not None:
            lines.append(f"MPU cap active: x{result.mpu_effective_cap:.3g}")
        else:
            lines.append("MPU cap active: no")
        lines.append(f"Conveyor constrained: {'yes' if result.conveyor_constrained else 'no'}")
        lines.append(f"Total estimate (/s): {format_cash_amount(result.estimated_total_value_per_second)}")
        lines.append(f"Tiles used by selected mines: {result.used_tiles}")
        lines.append(f"Tile limit: {result.tile_limit} ({result.tile_ratio * 100:.1f}% used)")
        lines.append(f"Dimension penalty: x{result.dimension_penalty:.3f}")
        lines.append(f"Synergy score: x{result.synergy_score:.3f}")
        lines.append(f"Selected upgraders: {len(result.upgrader_picks)}")
        lines.append(f"Selected furnace: {result.selected_furnace or 'none'}")
        lines.append(f"Limiter recommendation: {result.limiter_recommendation}")

        if result.target_value is not None:
            lines.append("")
            lines.append(f"Target: {format_cash_amount(result.target_value)}")
            if result.single_ore_base_value is not None:
                lines.append(f"Single ore base value: {format_cash_amount(result.single_ore_base_value)}")
            if result.single_ore_estimated_value is not None:
                lines.append(f"Single ore final estimate: {format_cash_amount(result.single_ore_estimated_value)}")

            if result.min_items_to_target is None:
                lines.append("Min items for single-ore target: n/a")
            elif result.min_items_to_target <= 0:
                lines.append("Min items for single-ore target: 0 (target <= base ore value)")
            else:
                cutoff = f" (through {result.min_items_target_item})" if result.min_items_target_item else ""
                lines.append(f"Min items for single-ore target: {result.min_items_to_target}{cutoff}")

            if result.target_too_small:
                lines.append("ETA from 0: skipped (target <= base single-ore value)")
            elif result.target_eta_overflow:
                lines.append("ETA from 0: >3m (not allowed)")
            elif result.target_seconds is None:
                lines.append("ETA from 0: n/a")
            else:
                lines.append(f"ETA from 0: {format_duration(result.target_seconds)}")

        if result.progression_opportunities:
            lines.append("")
            lines.append("Fusion/Evolution Progress")
            for idx, entry in enumerate(result.progression_opportunities[:25], start=1):
                readiness = "READY" if entry.ready_now else f"missing {len(entry.missing_names)}"
                lines.append(
                    f"{idx}. {entry.target_name} | {entry.target_tier} | "
                    f"{entry.owned_total}/{entry.required_total} ({entry.closeness * 100:.0f}%) {readiness}"
                )
                if entry.missing_names:
                    preview = ", ".join(token.title() for token in entry.missing_names[:5])
                    suffix = " ..." if len(entry.missing_names) > 5 else ""
                    lines.append(f"   Missing: {preview}{suffix}")
                else:
                    lines.append("   Missing: none")

        if result.notes:
            lines.append("")
            lines.append("Notes")
            for note in result.notes:
                lines.append(f"- {note}")

        summary = f"{len(result.mine_picks)} mine(s), {format_cash_amount(result.estimated_total_value_per_second)}/s estimate"
        self.summary_var.set(summary)
        self._set_output("\n".join(lines))

    def set_status(self, text: str) -> None:
        self.status_var.set(text)

    def _on_owned_tree_selected(self, _event: Any) -> None:
        selection = self.owned_tree.selection()
        row_id = str(selection[0]) if selection else None
        self._show_owned_item_details(row_id)

    def _on_owned_tree_click(self, event: Any) -> None:
        row_id = str(self.owned_tree.identify_row(event.y) or "")
        column = str(self.owned_tree.identify_column(event.x) or "")
        if not row_id or column != "#1":
            return
        row = self._owned_item_rows.get(row_id)
        if not isinstance(row, dict):
            return
        wiki_url = self._resolve_wiki_url(row)
        if not wiki_url:
            return
        self._open_wiki_url(wiki_url)

    def _on_owned_tree_activate(self, _event: Any) -> None:
        self._open_selected_wiki()

    def _on_owned_tree_motion(self, event: Any) -> None:
        row_id = str(self.owned_tree.identify_row(event.y) or "")
        column = str(self.owned_tree.identify_column(event.x) or "")
        pointer = ""
        if row_id and column == "#1":
            row = self._owned_item_rows.get(row_id)
            wiki_url = self._resolve_wiki_url(row if isinstance(row, dict) else None)
            if wiki_url:
                pointer = "hand2"
        self.owned_tree.configure(cursor=pointer)

    def _open_selected_wiki(self, _event: Any | None = None) -> None:
        if self._selected_wiki_url:
            self._open_wiki_url(self._selected_wiki_url)

    def _open_wiki_url(self, wiki_url: str) -> None:
        normalized = self._normalize_wiki_url(wiki_url)
        if not normalized:
            return
        try:
            webbrowser.open_new_tab(normalized)
        except Exception:
            pass

    def _resolve_wiki_url(self, row: dict[str, Any] | None) -> str:
        if not isinstance(row, dict):
            return ""
        raw = str(row.get("wiki_url") or "").strip()
        normalized = self._normalize_wiki_url(raw)
        if normalized:
            return normalized

        wiki_title = str(row.get("wiki_title") or row.get("name") or "").strip()
        if not wiki_title:
            return ""
        safe_title = quote(wiki_title.replace(" ", "_"), safe="()!,-._~")
        return f"https://minershaven.fandom.com/wiki/{safe_title}"

    def _normalize_wiki_url(self, url: str) -> str:
        text = str(url or "").strip()
        if not text:
            return ""
        if text.startswith("//"):
            return f"https:{text}"
        if text.startswith("/"):
            return f"https://minershaven.fandom.com{text}"
        if text.startswith("http://"):
            return "https://" + text[len("http://") :]
        if text.startswith("https://"):
            return text
        parsed = urlparse(text)
        if parsed.scheme and parsed.netloc:
            return text
        return ""

    def _show_owned_item_details(self, row_id: str | None) -> None:
        if not row_id:
            self.owned_title_var.set("Select an item")
            self._selected_wiki_url = ""
            self.owned_wiki_var.set("Wiki: -")
            self.owned_wiki_label.configure(style="Sub.TLabel", cursor="")
            self._set_owned_details("Choose an item in My Items to view more details.")
            return

        row = self._owned_item_rows.get(row_id)
        if not isinstance(row, dict):
            self._show_owned_item_details(None)
            return

        name = str(row.get("name") or "Unknown")
        quantity = max(0, int(row.get("quantity") or 0))
        tier = str(row.get("tier") or "-")
        item_type = str(row.get("item_type") or "-")
        mpu_text = str(row.get("mpu_text") or "-")
        multiplier_text = str(row.get("multiplier_text") or "-")
        wiki_url = self._resolve_wiki_url(row)

        self.owned_title_var.set(f"{name} x{quantity} | {item_type} | {tier}")

        self._selected_wiki_url = wiki_url
        if wiki_url:
            self.owned_wiki_var.set(f"Wiki: {wiki_url}")
            self.owned_wiki_label.configure(style="Link.TLabel", cursor="hand2")
        else:
            self.owned_wiki_var.set("Wiki: -")
            self.owned_wiki_label.configure(style="Sub.TLabel", cursor="")

        details_lines: list[str] = [
            "Core",
            f"- MPU: {mpu_text}",
            f"- Multiplier: {multiplier_text}",
            f"- Tier: {tier}",
            f"- Type: {item_type}",
        ]

        description = str(row.get("description") or "").strip()
        how_to_use = str(row.get("how_to_use") or "").strip()
        if description:
            details_lines.append("")
            details_lines.append("Description")
            details_lines.append(description)
        if how_to_use and normalize_lookup(how_to_use) != normalize_lookup(description):
            details_lines.append("")
            details_lines.append("How To Use")
            details_lines.append(how_to_use)

        size = row.get("size")
        if isinstance(size, dict) and size:
            details_lines.append("")
            details_lines.append("Size")
            details_lines.append(f"- Footprint: {size.get('footprint', '-')}")
            details_lines.append(f"- Height: {size.get('height', '-')}")
            details_lines.append(f"- Category: {size.get('category', '-')}")

        drop_rate = row.get("drop_rate")
        if isinstance(drop_rate, dict) and drop_rate:
            details_lines.append("")
            details_lines.append("Drop Rate")
            if drop_rate.get("per_second") is not None:
                details_lines.append(f"- Per second: {drop_rate.get('per_second')}")
            for key in ["kind", "text", "confidence"]:
                value = drop_rate.get(key)
                if value not in (None, ""):
                    details_lines.append(f"- {self._pretty_key(key)}: {value}")

        ore_worth = row.get("ore_worth")
        if isinstance(ore_worth, dict) and ore_worth:
            details_lines.append("")
            details_lines.append("Ore Worth")
            for key in ["kind", "value", "minimum", "maximum", "text", "confidence"]:
                value = ore_worth.get(key)
                if value not in (None, ""):
                    details_lines.append(f"- {self._pretty_key(key)}: {value}")

        self._append_detail_section(details_lines, "Proof & Limits", row.get("proof_and_limits"))
        self._append_detail_section(details_lines, "Acquisition", row.get("acquisition"))

        effect_tags = row.get("effect_tags")
        if isinstance(effect_tags, dict) and effect_tags:
            active_tags = [str(key) for key, value in effect_tags.items() if bool(value)]
            if active_tags:
                details_lines.append("")
                details_lines.append("Effect Tags")
                details_lines.append(", ".join(sorted(active_tags, key=str.lower)))

        self._set_owned_details("\n".join(details_lines))

    def _append_detail_section(self, lines: list[str], header: str, payload: Any) -> None:
        if not isinstance(payload, dict) or not payload:
            return
        rows: list[str] = []
        for key in sorted(payload.keys()):
            value = payload.get(key)
            if value in (None, ""):
                continue
            rows.append(f"- {self._pretty_key(str(key))}: {value}")
        if not rows:
            return
        lines.append("")
        lines.append(header)
        lines.extend(rows)

    def _pretty_key(self, key: str) -> str:
        text = str(key).replace("_", " ").strip()
        if not text:
            return ""
        return text[0].upper() + text[1:]

    def _set_owned_details(self, text: str) -> None:
        self.owned_details_text.configure(state=tk.NORMAL)
        self.owned_details_text.delete("1.0", tk.END)
        self.owned_details_text.insert(tk.END, text)
        self.owned_details_text.configure(state=tk.DISABLED)

    def _on_search_changed(self, *_args: Any) -> None:
        query = self.search_var.get().strip()
        self._refresh_suggestions(query)

    def _on_catalog_filter_changed(self, *_args: Any) -> None:
        self._sync_catalog_filter_controls()
        self._notify_state_changed()
        self._refresh_suggestions(self.search_var.get().strip())

    def _sync_catalog_filter_controls(self) -> None:
        mode = self.catalog_mode_var.get()
        self.catalog_tag_combo.configure(state="readonly" if mode == "tag" else "disabled")

    def _on_state_control_changed(self, *_args: Any) -> None:
        self._notify_state_changed()

    def _on_inventory_modified(self, _event: Any) -> None:
        if not bool(self.inventory_text.edit_modified()):
            return
        self.inventory_text.edit_modified(False)
        self._notify_state_changed()

    def _notify_state_changed(self) -> None:
        if self._suspend_change_notifications:
            return
        if self._change_callback is None:
            return
        try:
            self._change_callback()
        except Exception:
            pass

    def _refresh_suggestions(self, query: str) -> None:
        normalized_query = normalize_lookup(query)
        scored: list[tuple[int, int, bool, float, str]] = []

        tag_mode = self.catalog_mode_var.get() == "tag"
        selected_tag = self.catalog_tag_var.get().strip()

        filtered_entries = self._catalog_entries
        if tag_mode and selected_tag and selected_tag != "All tags":
            filtered_entries = [
                entry
                for entry in self._catalog_entries
                if selected_tag in (entry.get("tags") or [])
            ]

        if not normalized_query:
            for entry in filtered_entries[:80]:
                name = str(entry.get("name") or "")
                rank = int(entry.get("tier_rank") or 99)
                rarity_sort = self._as_float(entry.get("rarity_sort"))
                scored.append((50, rank, rarity_sort is None, rarity_sort if rarity_sort is not None else float("inf"), name))
        else:
            for entry in filtered_entries:
                name = str(entry.get("name") or "")
                normalized = str(entry.get("normalized") or "")
                if not normalized:
                    continue
                tier_rank = int(entry.get("tier_rank") or 99)
                rarity_sort = self._as_float(entry.get("rarity_sort"))
                score = 0
                if normalized == normalized_query:
                    score = 1000
                elif normalized.startswith(normalized_query):
                    score = 900 - len(normalized)
                elif normalized_query in normalized:
                    score = 700 - len(normalized)
                elif any(token.startswith(normalized_query) for token in normalized.split()):
                    score = 600 - len(normalized)
                if score > 0:
                    score -= tier_rank * 8
                    scored.append((score, tier_rank, rarity_sort is None, rarity_sort if rarity_sort is not None else float("inf"), name))

            scored.sort(key=lambda item: (-item[0], item[1], item[2], item[3], item[4].lower()))
            scored = scored[:80]

        self.suggestion_list.delete(0, tk.END)
        for _score, _rank, _rarity_none, _rarity_value, name in scored:
            self.suggestion_list.insert(tk.END, name)
        if self.suggestion_list.size() > 0:
            self.suggestion_list.selection_clear(0, tk.END)
            self.suggestion_list.selection_set(0)

    def _selected_or_typed_name(self) -> str | None:
        selection = self.suggestion_list.curselection()
        if selection:
            return str(self.suggestion_list.get(selection[0])).strip()
        typed = self.search_var.get().strip()
        return typed if typed else None

    def _add_selected_item(self) -> None:
        name = self._selected_or_typed_name()
        if not name:
            return
        qty = max(1, int(self.add_qty_var.get() or 1))
        parsed = self._parse_inventory_line(name)
        if parsed is not None:
            base_name, parsed_qty = parsed
            if parsed_qty > 1:
                name = base_name
                qty *= parsed_qty
        merged, display_name, total_qty = self._append_inventory_line(name, qty)
        if merged:
            self.set_status(f"Updated: {display_name} to x{total_qty}")
        else:
            self.set_status(f"Added: {display_name} x{total_qty}")

    def _add_typed_item(self) -> None:
        typed = self.search_var.get().strip()
        if not typed:
            return
        qty = max(1, int(self.add_qty_var.get() or 1))
        parsed = self._parse_inventory_line(typed)
        if parsed is not None:
            base_name, parsed_qty = parsed
            if parsed_qty > 1:
                typed = base_name
                qty *= parsed_qty
        merged, display_name, total_qty = self._append_inventory_line(typed, qty)
        if merged:
            self.set_status(f"Updated: {display_name} to x{total_qty}")
        else:
            self.set_status(f"Added: {display_name} x{total_qty}")

    def _append_inventory_line(self, name: str, qty: int) -> tuple[bool, str, int]:
        lines = [line.strip() for line in self.inventory_text.get("1.0", tk.END).splitlines() if line.strip()]
        entries: list[tuple[str, int]] = []
        target_norm = normalize_lookup(name)
        merged = False
        display_name = name
        total_qty = max(1, int(qty))

        for line in lines:
            parsed = self._parse_inventory_line(line)
            if parsed is None:
                continue

            existing_name, existing_qty = parsed
            if normalize_lookup(existing_name) == target_norm:
                merged_qty = existing_qty + qty
                entries.append((existing_name, merged_qty))
                merged = True
                display_name = existing_name
                total_qty = merged_qty
            else:
                entries.append((existing_name, existing_qty))

        if not merged:
            entries.append((name, qty))
            display_name = name
            total_qty = qty

        entries = self._sort_inventory_entries(entries)

        output_lines: list[str] = []
        for entry_name, entry_qty in entries:
            if entry_qty > 0:
                output_lines.append(f"{entry_name} x{entry_qty}")

        self.inventory_text.delete("1.0", tk.END)
        if output_lines:
            self.inventory_text.insert("1.0", "\n".join(output_lines))
        self._notify_state_changed()
        return (merged, display_name, max(1, int(total_qty)))

    def sort_inventory_lines(self) -> None:
        lines = [line.strip() for line in self.inventory_text.get("1.0", tk.END).splitlines() if line.strip()]
        if not lines:
            return

        merged: dict[str, tuple[str, int]] = {}
        for line in lines:
            parsed = self._parse_inventory_line(line)
            if parsed is None:
                continue
            item_name, item_qty = parsed
            key = normalize_lookup(item_name)
            previous = merged.get(key)
            if previous is None:
                merged[key] = (item_name, max(1, item_qty))
            else:
                merged[key] = (previous[0], previous[1] + max(1, item_qty))

        entries = self._sort_inventory_entries(list(merged.values()))
        output_lines = [f"{entry_name} x{entry_qty}" for entry_name, entry_qty in entries if entry_qty > 0]

        self.inventory_text.delete("1.0", tk.END)
        if output_lines:
            self.inventory_text.insert("1.0", "\n".join(output_lines))
        self.set_status("Sorted inventory by tier group + rarity")
        self._notify_state_changed()

    def _sort_inventory_entries(self, entries: list[tuple[str, int]]) -> list[tuple[str, int]]:
        return sorted(
            entries,
            key=lambda item: self._inventory_entry_sort_key(item[0]),
        )

    def _inventory_entry_sort_key(self, name: str) -> tuple[int, bool, float, str]:
        normalized = normalize_lookup(name)
        rank_label = self._catalog_sort_index.get(normalized)
        if rank_label is not None:
            return rank_label
        return (_CATALOG_TIER_ORDER["other"], True, float("inf"), normalized)

    def _parse_inventory_line(self, line: str) -> tuple[str, int] | None:
        text = line.strip()
        if not text:
            return None

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

    def _as_float(self, value: Any) -> float | None:
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            try:
                return float(text)
            except ValueError:
                return None
        return None

    def _remove_last_item(self) -> None:
        lines = [line for line in self.inventory_text.get("1.0", tk.END).splitlines() if line.strip()]
        if not lines:
            return
        lines.pop()
        self.inventory_text.delete("1.0", tk.END)
        if lines:
            self.inventory_text.insert("1.0", "\n".join(lines))
        self.set_status("Removed last item")
        self._notify_state_changed()

    def _clear_inventory_input(self) -> None:
        self.inventory_text.delete("1.0", tk.END)
        self.set_status("Cleared inventory input")
        self._notify_state_changed()

    def _set_output(self, text: str) -> None:
        self.output_text.configure(state=tk.NORMAL)
        self.output_text.delete("1.0", tk.END)
        self.output_text.insert(tk.END, text)
        self.output_text.configure(state=tk.DISABLED)

    def tick(self) -> None:
        self.root.update_idletasks()
        self.root.update()

    def run(self) -> None:
        self.root.mainloop()

    def _on_window_close(self) -> None:
        if self._close_callback is not None:
            try:
                self._close_callback()
            except Exception:
                pass
        try:
            self.root.destroy()
        except Exception:
            pass

    def _compute_geometry(self, width: int, height: int) -> str:
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 2)
        return f"{width}x{height}+{x}+{y}"
