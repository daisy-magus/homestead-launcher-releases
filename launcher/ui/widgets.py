"""Shared dark-theme constants and widget helpers."""
from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path

BG    = "#1a1a2e"
FG    = "#e8e8e8"
MUTED = "#888888"
BLUE  = "#4da6ff"
BTN   = "#2d2d44"
HOVER = "#3d3d5c"
RED   = "#ff6b6b"
FONT  = "Segoe UI"


def button(
    parent,
    text: str,
    cmd,
    primary: bool = False,
    danger: bool = False,
    full: bool = False,
) -> tk.Button:
    if primary:
        bg, fg, hover = BLUE,  "#000000", "#7fbfff"
    elif danger:
        bg, fg, hover = RED,   "#000000", "#ff8f8f"
    else:
        bg, fg, hover = BTN,   FG,        HOVER

    b = tk.Button(
        parent, text=text, command=cmd,
        bg=bg, fg=fg,
        font=(FONT, 10, "bold" if primary else "normal"),
        relief="flat", padx=16, pady=7, cursor="hand2",
        activebackground=hover, activeforeground=fg, bd=0,
    )
    if full:
        b.pack(fill="x", pady=3)
    return b


def center(root: "tk.Tk | tk.Toplevel", w: int, h: int) -> None:
    root.update_idletasks()
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    root.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")


def _asset_dir() -> Path:
    """Return the directory containing bundled assets regardless of run context."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "assets"  # type: ignore[attr-defined]
    return Path(__file__).parent.parent.parent / "assets"


def set_window_icon(root: "tk.Tk | tk.Toplevel") -> None:
    """Apply the Homestead icon to a tkinter window. Silent no-op if asset missing."""
    try:
        png = _asset_dir() / "homestead.png"
        if png.exists():
            img = tk.PhotoImage(file=str(png))
            root.iconphoto(True, img)
            root._hs_icon = img  # type: ignore[attr-defined]  # prevent GC
    except Exception:
        pass
