from __future__ import annotations

import re
import tkinter as tk
from tkinter import ttk
from typing import Any

from .calculator import CalculationResult, format_cash_amount, format_duration
from .utils import normalize_lookup


class PlannerWindow:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("MH Optimizer Planner")
        self.root.configure(bg="#0E121A")
        self.root.geometry(self._compute_geometry(980, 700))
        self.root.minsize(860, 620)
        self._close_callback: Any | None = None

        self._catalog_names: list[str] = []
        self._catalog_norm: list[tuple[str, str]] = []

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

        ttk.Label(controls, text="Target", style="Body.TLabel").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.target_var = tk.StringVar(value="7 de")
        ttk.Entry(controls, textvariable=self.target_var, width=18).grid(row=1, column=1, sticky="w", pady=(8, 0))

        ttk.Label(controls, text="Max mines", style="Body.TLabel").grid(row=1, column=2, sticky="w", pady=(8, 0), padx=(12, 0))
        self.max_mines_var = tk.IntVar(value=3)
        ttk.Spinbox(controls, from_=1, to=3, textvariable=self.max_mines_var, width=5).grid(row=1, column=3, sticky="w", pady=(8, 0))

        ttk.Label(controls, text="Loop cap", style="Body.TLabel").grid(row=1, column=4, sticky="w", pady=(8, 0), padx=(12, 0))
        self.loop_cap_var = tk.IntVar(value=4)
        ttk.Spinbox(controls, from_=1, to=10, textvariable=self.loop_cap_var, width=5).grid(row=1, column=5, sticky="w", pady=(8, 0))

        self.run_button = ttk.Button(controls, text="Calculate")
        self.run_button.grid(row=2, column=0, sticky="w", pady=(10, 0))

        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(controls, textvariable=self.status_var, style="Sub.TLabel").grid(row=2, column=1, columnspan=3, sticky="w", pady=(10, 0), padx=(8, 0))

        ttk.Label(right, text="Estimate", style="Header.TLabel").pack(anchor="w")
        self.summary_var = tk.StringVar(value="Run calculation to see output")
        ttk.Label(right, textvariable=self.summary_var, style="Body.TLabel", wraplength=380, justify=tk.LEFT).pack(anchor="w", pady=(0, 8))

        self.output_text = tk.Text(right, height=24, bg="#0E121A", fg="#CDE0FF", relief=tk.FLAT, font=("Consolas", 10), wrap=tk.WORD)
        self.output_text.pack(fill=tk.BOTH, expand=True)
        self.output_text.configure(state=tk.DISABLED)

    def set_run_callback(self, callback: Any) -> None:
        self.run_button.configure(command=callback)

    def set_close_callback(self, callback: Any) -> None:
        self._close_callback = callback

    def set_item_catalog(self, names: list[str]) -> None:
        unique = sorted({name.strip() for name in names if isinstance(name, str) and name.strip()})
        self._catalog_names = unique
        self._catalog_norm = [(name, normalize_lookup(name)) for name in unique]
        self._refresh_suggestions(self.search_var.get().strip())

    def get_inputs(self) -> tuple[str, str, int, bool, int, bool]:
        inventory = self.inventory_text.get("1.0", tk.END).strip()
        target_text = self.target_var.get().strip()
        max_mines = max(1, min(3, int(self.max_mines_var.get() or 3)))
        use_target = self.mode_var.get() == "time_to_target"
        loop_cap = max(1, min(10, int(self.loop_cap_var.get() or 4)))
        ban_destroy = bool(self.no_destroy_var.get())
        return (inventory, target_text, max_mines, use_target, loop_cap, ban_destroy)

    def get_state(self) -> dict[str, Any]:
        inventory, target_text, max_mines, use_target, loop_cap, ban_destroy = self.get_inputs()
        return {
            "inventory_text": inventory,
            "target_text": target_text,
            "max_mines": max_mines,
            "use_target_mode": use_target,
            "loop_cap": loop_cap,
            "ban_destroy": ban_destroy,
        }

    def apply_state(self, state: dict[str, Any]) -> None:
        if not isinstance(state, dict):
            return

        inventory_text = str(state.get("inventory_text") or "").strip()
        self.inventory_text.delete("1.0", tk.END)
        if inventory_text:
            self.inventory_text.insert("1.0", inventory_text)

        target_text = str(state.get("target_text") or "7 de").strip()
        self.target_var.set(target_text or "7 de")

        max_mines = state.get("max_mines", 3)
        try:
            self.max_mines_var.set(max(1, min(3, int(max_mines))))
        except Exception:
            self.max_mines_var.set(3)

        loop_cap = state.get("loop_cap", 4)
        try:
            self.loop_cap_var.set(max(1, min(10, int(loop_cap))))
        except Exception:
            self.loop_cap_var.set(4)

        use_target_mode = bool(state.get("use_target_mode", False))
        self.mode_var.set("time_to_target" if use_target_mode else "money_per_sec")

        self.no_destroy_var.set(bool(state.get("ban_destroy", False)))

    def show_result(self, result: CalculationResult) -> None:
        lines: list[str] = []
        lines.append("Selected Mines")
        for idx, pick in enumerate(result.mine_picks, start=1):
            ore_rate = "?" if pick.ore_per_second is None else f"{pick.ore_per_second:.4f}/s"
            lines.append(
                f"{idx}. {pick.name} | ore/s={ore_rate} | v/s={format_cash_amount(pick.expected_value_per_second)} | conf={pick.confidence:.2f}"
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
            flag_text = f" [{','.join(flags)}]" if flags else ""
            lines.append(
                f"{idx}. {pick.name} | x{pick.multiplier:g} | conf={pick.confidence:.2f}{flag_text}"
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
        lines.append(f"Total estimate: {format_cash_amount(result.estimated_total_value_per_second)} / s")
        lines.append(f"Tiles used by selected mines: {result.used_tiles}")
        lines.append(f"Tile limit: {result.tile_limit} ({result.tile_ratio * 100:.1f}% used)")
        lines.append(f"Dimension penalty: x{result.dimension_penalty:.3f}")
        lines.append(f"Synergy score: x{result.synergy_score:.3f}")
        lines.append(f"Selected upgraders: {len(result.upgrader_picks)}")
        lines.append(f"Limiter recommendation: {result.limiter_recommendation}")

        if result.target_value is not None:
            lines.append("")
            lines.append(f"Target: {format_cash_amount(result.target_value)}")
            if result.target_seconds is None:
                lines.append("ETA: n/a")
            else:
                lines.append(f"ETA from 0: {format_duration(result.target_seconds)}")

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

    def _on_search_changed(self, *_args: Any) -> None:
        query = self.search_var.get().strip()
        self._refresh_suggestions(query)

    def _refresh_suggestions(self, query: str) -> None:
        normalized_query = normalize_lookup(query)
        scored: list[tuple[int, str]] = []

        if not normalized_query:
            for name in self._catalog_names[:40]:
                scored.append((50, name))
        else:
            for name, normalized in self._catalog_norm:
                if not normalized:
                    continue
                if normalized == normalized_query:
                    scored.append((1000, name))
                    continue
                if normalized.startswith(normalized_query):
                    scored.append((900 - len(normalized), name))
                    continue
                if normalized_query in normalized:
                    scored.append((700 - len(normalized), name))
                    continue
                tokens = normalized.split()
                if any(token.startswith(normalized_query) for token in tokens):
                    scored.append((600 - len(normalized), name))

            scored.sort(key=lambda item: item[0], reverse=True)
            scored = scored[:50]

        self.suggestion_list.delete(0, tk.END)
        for _score, name in scored:
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
        self._append_inventory_line(name, qty)
        self.set_status(f"Added: {name} x{qty}")

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
        self._append_inventory_line(typed, qty)
        self.set_status(f"Added typed: {typed} x{qty}")

    def _append_inventory_line(self, name: str, qty: int) -> None:
        lines = [line.strip() for line in self.inventory_text.get("1.0", tk.END).splitlines() if line.strip()]
        entries: list[tuple[str, int]] = []
        target_norm = normalize_lookup(name)
        merged = False

        for line in lines:
            parsed = self._parse_inventory_line(line)
            if parsed is None:
                continue

            existing_name, existing_qty = parsed
            if normalize_lookup(existing_name) == target_norm:
                entries.append((existing_name, existing_qty + qty))
                merged = True
            else:
                entries.append((existing_name, existing_qty))

        if not merged:
            entries.append((name, qty))

        output_lines: list[str] = []
        for entry_name, entry_qty in entries:
            if entry_qty > 0:
                output_lines.append(f"{entry_name} x{entry_qty}")

        self.inventory_text.delete("1.0", tk.END)
        if output_lines:
            self.inventory_text.insert("1.0", "\n".join(output_lines))

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

    def _remove_last_item(self) -> None:
        lines = [line for line in self.inventory_text.get("1.0", tk.END).splitlines() if line.strip()]
        if not lines:
            return
        lines.pop()
        self.inventory_text.delete("1.0", tk.END)
        if lines:
            self.inventory_text.insert("1.0", "\n".join(lines))
        self.set_status("Removed last item")

    def _clear_inventory_input(self) -> None:
        self.inventory_text.delete("1.0", tk.END)
        self.set_status("Cleared inventory input")

    def _set_output(self, text: str) -> None:
        self.output_text.configure(state=tk.NORMAL)
        self.output_text.delete("1.0", tk.END)
        self.output_text.insert(tk.END, text)
        self.output_text.configure(state=tk.DISABLED)

    def tick(self) -> None:
        self.root.update_idletasks()
        self.root.update()

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
