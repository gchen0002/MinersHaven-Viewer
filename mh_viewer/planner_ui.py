from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any

from .calculator import CalculationResult, format_cash_amount, format_duration


class PlannerWindow:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("MH Optimizer Planner")
        self.root.configure(bg="#0E121A")
        self.root.geometry(self._compute_geometry(980, 700))
        self.root.minsize(860, 620)

        self._build_layout()

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

        self.inventory_text = tk.Text(left, height=16, bg="#0E121A", fg="#DCE6F8", insertbackground="#DCE6F8", wrap=tk.WORD)
        self.inventory_text.pack(fill=tk.BOTH, expand=True)

        controls = ttk.Frame(left, style="Card.TFrame")
        controls.pack(fill=tk.X, pady=(10, 0))

        self.mode_var = tk.StringVar(value="money_per_sec")
        ttk.Radiobutton(controls, text="Money/sec", variable=self.mode_var, value="money_per_sec").grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(controls, text="Time-to-target", variable=self.mode_var, value="time_to_target").grid(row=0, column=1, sticky="w", padx=(12, 0))

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

    def get_inputs(self) -> tuple[str, str, int, bool, int]:
        inventory = self.inventory_text.get("1.0", tk.END).strip()
        target_text = self.target_var.get().strip()
        max_mines = max(1, min(3, int(self.max_mines_var.get() or 3)))
        use_target = self.mode_var.get() == "time_to_target"
        loop_cap = max(1, min(10, int(self.loop_cap_var.get() or 4)))
        return (inventory, target_text, max_mines, use_target, loop_cap)

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
        lines.append("Aggregate")
        lines.append(f"Mine value/sec: {format_cash_amount(result.mine_expected_value_per_second)}")
        lines.append(f"Base ore rate: {result.base_ores_per_second:.4f}/s")
        lines.append(f"Effective ore rate: {result.effective_ores_per_second:.4f}/s")
        lines.append(f"Bottleneck multiplier: x{result.bottleneck_multiplier:.3g}")
        lines.append(f"Chain multiplier estimate: x{result.estimated_multiplier:.3g}")
        lines.append(f"Loop passes used: {result.loop_passes}")
        lines.append(f"Total estimate: {format_cash_amount(result.estimated_total_value_per_second)} / s")
        lines.append(f"Tiles used by selected mines: {result.used_tiles}")
        lines.append(f"Selected upgraders: {len(result.upgrader_picks)}")

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

    def _set_output(self, text: str) -> None:
        self.output_text.configure(state=tk.NORMAL)
        self.output_text.delete("1.0", tk.END)
        self.output_text.insert(tk.END, text)
        self.output_text.configure(state=tk.DISABLED)

    def tick(self) -> None:
        self.root.update_idletasks()
        self.root.update()

    def _compute_geometry(self, width: int, height: int) -> str:
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 2)
        return f"{width}x{height}+{x}+{y}"
