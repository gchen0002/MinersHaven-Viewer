from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any


class ViewerWindow:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("MH Viewer")
        self.root.attributes("-topmost", True)
        self.root.configure(bg="#111318")
        self.root.geometry(self._compute_geometry(430, 520))
        self.root.resizable(False, True)

        self._details_visible = False
        self._build_layout()

    def _build_layout(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("Card.TFrame", background="#1A1E26")
        style.configure("Header.TLabel", background="#1A1E26", foreground="#F5F7FA", font=("Segoe UI", 15, "bold"))
        style.configure("Sub.TLabel", background="#1A1E26", foreground="#9EA7B8", font=("Segoe UI", 10))
        style.configure("Body.TLabel", background="#1A1E26", foreground="#E2E8F0", font=("Segoe UI", 10))

        container = ttk.Frame(self.root, style="Card.TFrame", padding=12)
        container.pack(fill=tk.BOTH, expand=True)

        self.name_var = tk.StringVar(value="No item matched")
        self.core_var = tk.StringVar(value="Hold ALT while hovering an item in inventory.")
        self.mult_var = tk.StringVar(value="Multiplier: -")
        self.mpu_var = tk.StringVar(value="MPU: -")
        self.size_var = tk.StringVar(value="Size: -")
        self.type_var = tk.StringVar(value="Type: -")
        self.debug_var = tk.StringVar(value="Debug: waiting")

        ttk.Label(container, textvariable=self.name_var, style="Header.TLabel").pack(anchor="w")
        ttk.Label(container, textvariable=self.type_var, style="Sub.TLabel").pack(anchor="w", pady=(0, 8))
        ttk.Label(container, textvariable=self.core_var, wraplength=390, style="Body.TLabel", justify=tk.LEFT).pack(anchor="w")

        ttk.Separator(container, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=8)
        ttk.Label(container, textvariable=self.mult_var, style="Body.TLabel").pack(anchor="w")
        ttk.Label(container, textvariable=self.mpu_var, style="Body.TLabel").pack(anchor="w")
        ttk.Label(container, textvariable=self.size_var, style="Body.TLabel").pack(anchor="w")
        ttk.Label(container, textvariable=self.debug_var, style="Sub.TLabel", wraplength=390).pack(anchor="w", pady=(6, 0))

        self.toggle_btn = ttk.Button(container, text="More Details", command=self._toggle_details)
        self.toggle_btn.pack(anchor="w", pady=(12, 4))

        self.details_text = tk.Text(
            container,
            height=16,
            width=50,
            bg="#111318",
            fg="#C5D0E0",
            relief=tk.FLAT,
            font=("Consolas", 10),
            wrap=tk.WORD,
        )
        self.details_text.pack(fill=tk.BOTH, expand=True)
        self.details_text.configure(state=tk.DISABLED)
        self.details_text.pack_forget()

    def _toggle_details(self) -> None:
        self._details_visible = not self._details_visible
        if self._details_visible:
            self.details_text.pack(fill=tk.BOTH, expand=True)
            self.toggle_btn.configure(text="Hide Details")
        else:
            self.details_text.pack_forget()
            self.toggle_btn.configure(text="More Details")

    def update_item(self, item: dict[str, Any], score: int | None = None) -> None:
        self.name_var.set(item.get("name", "Unknown"))
        item_type = item.get("type") or "Unknown"
        tier = item.get("tier") or "-"
        score_text = f" | Match {score}%" if score is not None else ""
        self.type_var.set(f"{item_type} | {tier}{score_text}")

        description = item.get("description") or item.get("how_to_use") or "No description available."
        self.core_var.set(description)

        mult = item.get("multiplier") or {}
        mpu = item.get("mpu") or {}
        size = item.get("size") or {}
        self.mult_var.set(f"Multiplier: {mult.get('text', '-')}")
        self.mpu_var.set(f"MPU: {mpu.get('text', '-')}")
        self.size_var.set(f"Size: {size.get('footprint', '-')} h={size.get('height', '-')}")

        details_lines = []
        details = item.get("details") or {}
        tags = details.get("tags") or {}
        proof = item.get("proof_and_limits") or {}
        acquisition = item.get("acquisition") or {}
        effect_tags = item.get("effect_tags") or {}

        details_lines.append("Proof & Limits")
        for key, value in proof.items():
            details_lines.append(f"- {key}: {value}")

        details_lines.append("\nAcquisition")
        for key, value in acquisition.items():
            details_lines.append(f"- {key}: {value}")

        elements = details.get("elements") or {}
        named = elements.get("named") or {}
        if named:
            details_lines.append("\nElements")
            for key, value in named.items():
                details_lines.append(f"- {key}: {value}")

        if effect_tags:
            details_lines.append("\nEffect Tags")
            for key in sorted(effect_tags.keys()):
                details_lines.append(f"- {key}")

        details_lines.append("\nGeneral Tags")
        for key, value in tags.items():
            details_lines.append(f"- {key}: {value}")

        details_lines.append(f"\nWiki: {item.get('wiki_url', '-')}")

        self.details_text.configure(state=tk.NORMAL)
        self.details_text.delete("1.0", tk.END)
        self.details_text.insert(tk.END, "\n".join(details_lines))
        self.details_text.configure(state=tk.DISABLED)

    def show_status(self, text: str) -> None:
        self.name_var.set("MH Viewer")
        self.type_var.set("Status")
        self.core_var.set(text)

    def set_debug(self, text: str) -> None:
        self.debug_var.set(text)

    def tick(self) -> None:
        self.root.update_idletasks()
        self.root.update()

    def _compute_geometry(self, width: int, height: int) -> str:
        screen_width = self.root.winfo_screenwidth()
        x = max(0, screen_width - width - 40)
        y = 40
        return f"{width}x{height}+{x}+{y}"
