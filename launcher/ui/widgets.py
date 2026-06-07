"""Shared dark-theme constants and widget helpers."""
from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path

# Arcane-share palette
BG     = "#07070b"
PANEL  = "#12141e"
FG     = "#c9d2e0"
MUTED  = "#6b7488"
CYAN   = "#5fe3ff"
VIOLET = "#b083ff"
BTN    = "#12141e"
HOVER  = "#1a2535"
RED    = "#ff7a93"
BORDER = "#1a2535"   # ~rgba(120,200,255,0.14) composited over #07070b

BLUE = CYAN          # backward-compat alias

FONT = "Consolas" if sys.platform == "win32" else "Monospace"


def button(
    parent,
    text: str,
    cmd,
    primary: bool = False,
    danger: bool = False,
    full: bool = False,
) -> tk.Button:
    if primary:
        bg, fg, hbg = CYAN,  "#07070b", "#8eeeff"
        label = text.upper()
    elif danger:
        bg, fg, hbg = RED,   "#07070b", "#ff9aaa"
        label = text
    else:
        bg, fg, hbg = BTN,   CYAN,      HOVER
        label = text

    b = tk.Button(
        parent, text=label, command=cmd,
        bg=bg, fg=fg,
        font=(FONT, 9, "bold"),
        relief="flat", padx=16, pady=7, cursor="hand2",
        activebackground=hbg, activeforeground=fg, bd=0,
    )
    b.bind("<Enter>", lambda _e: b.config(bg=hbg))
    b.bind("<Leave>", lambda _e: b.config(bg=bg))
    if full:
        b.pack(fill="x", pady=3)
    return b


def bordered_frame(parent, **pack_kw) -> tuple[tk.Frame, tk.Frame]:
    """Return (border_frame, inner_frame). Pack border_frame; add widgets to inner_frame."""
    outer = tk.Frame(parent, bg=BORDER)
    inner = tk.Frame(outer, bg=PANEL)
    inner.pack(fill="both", expand=True, padx=1, pady=1)
    return outer, inner


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
