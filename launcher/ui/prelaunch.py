"""
Pre-launch window: account selection, RAM slider, optional mod changelog.

Runs on the main thread (blocks via mainloop).
Returns a result dict:
  {"cancelled": True}
  {"ram_gb": int, "account": MinecraftAccount}
  {"ram_gb": int, "account": None, "pending_microsoft": True}
"""
from __future__ import annotations

import json
import sys
import tkinter as tk
from tkinter import ttk
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..auth import MinecraftAccount

from .widgets import (
    BG, PANEL, FG, MUTED, CYAN, VIOLET, BTN, HOVER, RED, BORDER, FONT,
    button, bordered_frame, center, set_window_icon,
)


# ── RAM detection ──────────────────────────────────────────────────────────────

def _detect_ram_gb() -> int:
    try:
        if sys.platform == "win32":
            import ctypes
            class _MEMSTATEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength",                ctypes.c_ulong),
                    ("dwMemoryLoad",            ctypes.c_ulong),
                    ("ullTotalPhys",            ctypes.c_ulonglong),
                    ("ullAvailPhys",            ctypes.c_ulonglong),
                    ("ullTotalPageFile",        ctypes.c_ulonglong),
                    ("ullAvailPageFile",        ctypes.c_ulonglong),
                    ("ullTotalVirtual",         ctypes.c_ulonglong),
                    ("ullAvailVirtual",         ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]
            stat = _MEMSTATEX()
            stat.dwLength = ctypes.sizeof(stat)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            return int(stat.ullTotalPhys / (1024 ** 3))
        else:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        return int(line.split()[1]) // (1024 * 1024)
    except Exception:
        pass
    return 8


def _ram_range(total_gb: int) -> tuple[int, int, int]:
    """(min_gb, default_gb, max_gb) for the slider."""
    max_gb = max(4, total_gb - 2)
    default_gb = min(max(3, total_gb // 3), max_gb)
    return 2, default_gb, max_gb


def _load_ram_pref(prefs: Path, min_gb: int, max_gb: int, fallback: int) -> int:
    try:
        data = json.loads(prefs.read_text())
        return max(min_gb, min(int(data["ram_gb"]), max_gb))
    except Exception:
        return fallback


def _save_ram_pref(prefs: Path, ram_gb: int) -> None:
    try:
        data = json.loads(prefs.read_text()) if prefs.exists() else {}
    except Exception:
        data = {}
    data["ram_gb"] = ram_gb
    prefs.write_text(json.dumps(data))


# ── Window ─────────────────────────────────────────────────────────────────────

class PreLaunchWindow:

    def __init__(
        self,
        saved_account: "MinecraftAccount | None",
        changelog: str | None = None,
    ) -> None:
        self._saved = saved_account
        self._changelog = changelog
        self._account: "MinecraftAccount | None" = saved_account
        self._result: dict = {"cancelled": True}
        self._root: tk.Tk | None = None
        self._ram_var: tk.IntVar | None = None
        self._acct_name_var: tk.StringVar | None = None
        self._acct_type_var: tk.StringVar | None = None

    def run(self) -> dict:
        from ..config import VERSION
        root = tk.Tk(className="BetterLauncher")
        root.title(f"Better Launcher  v{VERSION}")
        root.resizable(True, True)
        root.configure(bg=BG)
        set_window_icon(root)
        root.protocol("WM_DELETE_WINDOW", lambda: self._exit(root))
        self._root = root

        from ..config import prefs_file
        total_ram = _detect_ram_gb()
        ram_min, ram_def, ram_max = _ram_range(total_ram)
        saved_ram = _load_ram_pref(prefs_file(), ram_min, ram_max, ram_def)
        self._ram_var = tk.IntVar(value=saved_ram)

        # ── Header ────────────────────────────────────────────────────────
        tk.Label(
            root, text="BETTER LAUNCHER", bg=BG, fg=CYAN,
            font=(FONT, 16, "bold"),
        ).pack(pady=(18, 2))
        tk.Label(
            root, text="PRIVATE MODDED SERVER", bg=BG, fg=MUTED,
            font=(FONT, 8),
        ).pack(pady=(0, 10))

        content = tk.Frame(root, bg=BG)
        content.pack(fill="both", expand=True, padx=20)

        self._build_account_card(content)
        self._build_ram_card(content, total_ram, ram_min, ram_max, saved_ram)

        if self._changelog:
            self._build_changelog_card(content, self._changelog)

        # ── Launch button ─────────────────────────────────────────────────
        button(root, "Launch", self._on_launch, primary=True).pack(pady=(10, 18))

        root.update_idletasks()
        w = max(390, root.winfo_reqwidth())
        h = max(280, root.winfo_reqheight())
        root.minsize(320, h)
        center(root, w, h)
        root.mainloop()
        return self._result

    # ── Account card ──────────────────────────────────────────────────────

    def _build_account_card(self, parent: tk.Frame) -> None:
        border, frame = bordered_frame(parent)
        border.pack(fill="x", pady=4)

        inner = tk.Frame(frame, bg=PANEL, padx=14, pady=10)
        inner.pack(fill="x")

        self._acct_name_var = tk.StringVar()
        self._acct_type_var = tk.StringVar()
        self._refresh_account_vars()

        tk.Label(inner, textvariable=self._acct_name_var,
                 bg=PANEL, fg=FG, font=(FONT, 11, "bold")).pack(anchor="w")
        tk.Label(inner, textvariable=self._acct_type_var,
                 bg=PANEL, fg=VIOLET, font=(FONT, 8)).pack(anchor="w")

        btn_text = "Switch account" if self._saved else "Sign in"
        button(inner, btn_text, self._open_account_picker).pack(anchor="w", pady=(8, 0))

    def _refresh_account_vars(self) -> None:
        if self._account:
            self._acct_name_var.set(self._account.username)
            self._acct_type_var.set(
                "OFFLINE" if self._account.offline else "MICROSOFT"
            )
        else:
            self._acct_name_var.set("No account selected")
            self._acct_type_var.set("SIGN IN TO CONTINUE")

    def _open_account_picker(self) -> None:
        top = tk.Toplevel(self._root)
        top.title("Account")
        top.resizable(False, False)
        top.configure(bg=BG)
        top.grab_set()
        center(top, 320, 240)

        body = tk.Frame(top, bg=BG)
        body.pack(fill="both", expand=True, padx=24, pady=16)

        def clear():
            for w in body.winfo_children():
                w.destroy()

        def show_login_choice():
            clear()
            tk.Label(body, text="HOW WOULD YOU LIKE TO PLAY?",
                     bg=BG, fg=MUTED, font=(FONT, 8)).pack(pady=(6, 12))
            button(body, "Sign in with Microsoft", pick_microsoft,
                   primary=True, full=True)
            button(body, "Play offline  (free)", show_offline, full=True)

        auto_launch = [False]

        def pick_microsoft():
            self._account = None
            self._result["pending_microsoft"] = True
            auto_launch[0] = True
            top.destroy()

        def show_offline():
            clear()
            tk.Label(body, text="CHOOSE A USERNAME",
                     bg=BG, fg=MUTED, font=(FONT, 8)).pack(pady=(6, 8))

            var = tk.StringVar()
            entry = tk.Entry(body, textvariable=var, bg=PANEL, fg=FG,
                             font=(FONT, 11), relief="flat",
                             insertbackground=CYAN, bd=0,
                             highlightthickness=1, highlightbackground=BORDER,
                             highlightcolor=CYAN)
            entry.pack(fill="x", ipady=7)
            entry.focus_set()

            err = tk.Label(body, text="", bg=BG, fg=RED, font=(FONT, 8))
            err.pack(pady=(4, 0))

            row = tk.Frame(body, bg=BG)
            row.pack(pady=8)

            def confirm(event=None):
                name = var.get().strip()
                if not name:
                    err.config(text="Username cannot be empty.")
                    return
                from ..auth import login_offline, save_account
                from ..config import auth_file
                acc = login_offline(name)
                save_account(auth_file(), acc)
                self._account = acc
                self._refresh_account_vars()
                top.destroy()

            entry.bind("<Return>", confirm)
            button(row, "Back", show_login_choice).pack(side="left", padx=4)
            button(row, "Continue", confirm, primary=True).pack(side="left", padx=4)

        if self._account:
            kind = "OFFLINE" if self._account.offline else "MICROSOFT"
            tk.Label(body, text=self._account.username,
                     bg=BG, fg=FG, font=(FONT, 13, "bold")).pack(pady=(6, 2))
            tk.Label(body, text=kind,
                     bg=BG, fg=VIOLET, font=(FONT, 8)).pack()
            row = tk.Frame(body, bg=BG)
            row.pack(pady=14)
            button(row, "Keep", top.destroy, primary=True).pack(side="left", padx=4)
            button(row, "Switch", show_login_choice).pack(side="left", padx=4)
        else:
            show_login_choice()

        top.wait_window()
        self._refresh_account_vars()
        if auto_launch[0]:
            self._on_launch()

    # ── RAM card ───────────────────────────────────────────────────────────

    def _build_ram_card(
        self, parent: tk.Frame,
        total_gb: int, min_gb: int, max_gb: int, default_gb: int,
    ) -> None:
        border, frame = bordered_frame(parent)
        border.pack(fill="x", pady=4)

        inner = tk.Frame(frame, bg=PANEL, padx=14, pady=10)
        inner.pack(fill="x")

        header = tk.Frame(inner, bg=PANEL)
        header.pack(fill="x")
        tk.Label(header, text="RAM", bg=PANEL, fg=FG,
                 font=(FONT, 10, "bold")).pack(side="left")
        tk.Label(header, text=f"  {total_gb} GB installed",
                 bg=PANEL, fg=MUTED, font=(FONT, 8)).pack(side="left")

        val_lbl = tk.Label(inner, text=f"{default_gb} GB",
                           bg=PANEL, fg=CYAN, font=(FONT, 10, "bold"))
        val_lbl.pack(anchor="e")

        style = ttk.Style()
        style.theme_use("default")
        style.configure(
            "HS.Horizontal.TScale",
            troughcolor=BG,
            background=CYAN,
            sliderthickness=14,
        )

        slider = ttk.Scale(
            inner, from_=min_gb, to=max_gb,
            variable=self._ram_var, orient="horizontal",
            style="HS.Horizontal.TScale",
            command=lambda v: val_lbl.config(text=f"{int(float(v))} GB"),
        )
        slider.pack(fill="x", pady=(4, 2))

        tk.Label(
            inner,
            text=f"default {default_gb} GB  ·  max {max_gb} GB",
            bg=PANEL, fg=MUTED, font=(FONT, 7),
        ).pack(anchor="w")

    # ── Changelog card ─────────────────────────────────────────────────────

    def _build_changelog_card(self, parent: tk.Frame, text: str) -> None:
        border, frame = bordered_frame(parent)
        border.pack(fill="x", pady=4)

        inner = tk.Frame(frame, bg=PANEL, padx=14, pady=8)
        inner.pack(fill="x")

        tk.Label(inner, text="MODS UPDATED SINCE YOUR LAST LAUNCH",
                 bg=PANEL, fg=MUTED, font=(FONT, 7)).pack(anchor="w", pady=(0, 4))

        txt = tk.Text(inner, bg=PANEL, fg=FG, font=(FONT, 9),
                      height=4, relief="flat", wrap="word", padx=4, pady=2)
        txt.insert("1.0", text)
        txt.config(state="disabled")
        txt.pack(fill="x")

    # ── Actions ────────────────────────────────────────────────────────────

    def _on_launch(self) -> None:
        if not self._account and not self._result.get("pending_microsoft"):
            self._open_account_picker()
            if not self._account and not self._result.get("pending_microsoft"):
                return

        from ..config import prefs_file
        ram_gb = int(self._ram_var.get())
        _save_ram_pref(prefs_file(), ram_gb)
        self._result = {
            "cancelled": False,
            "ram_gb":   ram_gb,
            "account":  self._account,
            "pending_microsoft": self._result.get("pending_microsoft", False),
        }
        self._root.destroy()

    def _exit(self, root: tk.Tk) -> None:
        self._result = {"cancelled": True}
        root.destroy()
