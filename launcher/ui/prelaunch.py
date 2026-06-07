"""
Pre-launch window: account selection, RAM slider, optional mod changelog.

Runs on the main thread (blocks via mainloop).
Returns a result dict:
  {"cancelled": True}
  {"ram_gb": int, "account": MinecraftAccount}
  {"ram_gb": int, "account": None, "pending_microsoft": True}
"""
from __future__ import annotations

import sys
import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..auth import MinecraftAccount

from .widgets import BG, FG, MUTED, BLUE, BTN, RED, FONT, button, center, set_window_icon


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
    max_gb = max(4, total_gb - 2)       # leave ~2 GB for the OS
    default_gb = min(max(3, total_gb // 3), max_gb)
    return 2, default_gb, max_gb


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
        root = tk.Tk(className="Homestead")
        root.title("Homestead")
        root.resizable(True, True)
        root.configure(bg=BG)
        set_window_icon(root)
        root.protocol("WM_DELETE_WINDOW", lambda: self._exit(root))
        self._root = root

        total_ram = _detect_ram_gb()
        ram_min, ram_def, ram_max = _ram_range(total_ram)
        self._ram_var = tk.IntVar(value=ram_def)

        # ── Header ────────────────────────────────────────────────────────
        tk.Label(root, text="Homestead", bg=BG, fg=BLUE,
                 font=(FONT, 15, "bold")).pack(pady=(14, 1))
        tk.Label(root, text="Private Modded Server", bg=BG, fg=MUTED,
                 font=(FONT, 9)).pack(pady=(0, 8))

        content = tk.Frame(root, bg=BG)
        content.pack(fill="both", expand=True, padx=20)

        self._build_account_card(content)
        self._build_ram_card(content, total_ram, ram_min, ram_max, ram_def)

        if self._changelog:
            self._build_changelog_card(content, self._changelog)

        # ── Launch button ─────────────────────────────────────────────────
        button(root, "LAUNCH", self._on_launch, primary=True).pack(pady=(10, 16))

        # Let tkinter compute natural size, then center that
        root.update_idletasks()
        w = max(380, root.winfo_reqwidth())
        h = max(280, root.winfo_reqheight())
        root.minsize(320, h)
        center(root, w, h)
        root.mainloop()
        return self._result

    # ── Account card ──────────────────────────────────────────────────────

    def _build_account_card(self, parent: tk.Frame) -> None:
        frame = tk.Frame(parent, bg=BTN, padx=14, pady=10)
        frame.pack(fill="x", pady=4)

        self._acct_name_var = tk.StringVar()
        self._acct_type_var = tk.StringVar()
        self._refresh_account_vars()

        tk.Label(frame, textvariable=self._acct_name_var,
                 bg=BTN, fg=FG, font=(FONT, 11, "bold")).pack(anchor="w")
        tk.Label(frame, textvariable=self._acct_type_var,
                 bg=BTN, fg=MUTED, font=(FONT, 8)).pack(anchor="w")

        btn_text = "Switch account" if self._saved else "Sign in"
        button(frame, btn_text, self._open_account_picker).pack(anchor="w", pady=(6, 0))

    def _refresh_account_vars(self) -> None:
        if self._account:
            self._acct_name_var.set(self._account.username)
            self._acct_type_var.set(
                "Offline account" if self._account.offline else "Microsoft account"
            )
        else:
            self._acct_name_var.set("No account selected")
            self._acct_type_var.set("Sign in before launching")

    def _open_account_picker(self) -> None:
        top = tk.Toplevel(self._root)
        top.title("Account")
        top.resizable(False, False)
        top.configure(bg=BG)
        top.grab_set()
        center(top, 320, 240)

        body = tk.Frame(top, bg=BG)
        body.pack(fill="both", expand=True, padx=24, pady=14)

        def clear():
            for w in body.winfo_children():
                w.destroy()

        def show_login_choice():
            clear()
            tk.Label(body, text="How would you like to play?",
                     bg=BG, fg=MUTED, font=(FONT, 10)).pack(pady=(6, 10))
            button(body, "Sign in with Microsoft", pick_microsoft,
                   primary=True, full=True)
            button(body, "Play offline  (free)", show_offline, full=True)

        def pick_microsoft():
            self._account = None
            self._result["pending_microsoft"] = True
            top.destroy()

        def show_offline():
            clear()
            tk.Label(body, text="Choose a username",
                     bg=BG, fg=FG, font=(FONT, 10)).pack(pady=(6, 6))

            var = tk.StringVar()
            entry = tk.Entry(body, textvariable=var, bg=BTN, fg=FG,
                             font=(FONT, 11), relief="flat",
                             insertbackground=FG, bd=0)
            entry.pack(fill="x", ipady=7)
            entry.focus_set()

            err = tk.Label(body, text="", bg=BG, fg=RED, font=(FONT, 8))
            err.pack(pady=(2, 0))

            row = tk.Frame(body, bg=BG)
            row.pack(pady=6)

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
            kind = "Offline" if self._account.offline else "Microsoft"
            tk.Label(body, text=self._account.username,
                     bg=BG, fg=FG, font=(FONT, 13, "bold")).pack(pady=(6, 2))
            tk.Label(body, text=kind,
                     bg=BG, fg=MUTED, font=(FONT, 9)).pack()
            row = tk.Frame(body, bg=BG)
            row.pack(pady=14)
            button(row, "Keep", top.destroy, primary=True).pack(side="left", padx=4)
            button(row, "Switch", show_login_choice).pack(side="left", padx=4)
        else:
            show_login_choice()

        top.wait_window()
        self._refresh_account_vars()

    # ── RAM card ───────────────────────────────────────────────────────────

    def _build_ram_card(
        self, parent: tk.Frame,
        total_gb: int, min_gb: int, max_gb: int, default_gb: int,
    ) -> None:
        frame = tk.Frame(parent, bg=BTN, padx=14, pady=10)
        frame.pack(fill="x", pady=4)

        header = tk.Frame(frame, bg=BTN)
        header.pack(fill="x")
        tk.Label(header, text="RAM", bg=BTN, fg=FG,
                 font=(FONT, 10, "bold")).pack(side="left")
        tk.Label(header, text=f"  ({total_gb} GB installed)",
                 bg=BTN, fg=MUTED, font=(FONT, 8)).pack(side="left")

        val_lbl = tk.Label(frame, text=f"{default_gb} GB",
                           bg=BTN, fg=BLUE, font=(FONT, 10, "bold"))
        val_lbl.pack(anchor="e")

        style = ttk.Style()
        style.theme_use("default")
        style.configure("HS.Horizontal.TScale", troughcolor=BG, background=BLUE)

        slider = ttk.Scale(
            frame, from_=min_gb, to=max_gb,
            variable=self._ram_var, orient="horizontal",
            style="HS.Horizontal.TScale",
            command=lambda v: val_lbl.config(text=f"{int(float(v))} GB"),
        )
        slider.pack(fill="x", pady=(4, 2))

        tk.Label(
            frame,
            text=f"Default {default_gb} GB · max {max_gb} GB ({total_gb} GB installed)",
            bg=BTN, fg=MUTED, font=(FONT, 7),
        ).pack(anchor="w")

    # ── Changelog card ─────────────────────────────────────────────────────

    def _build_changelog_card(self, parent: tk.Frame, text: str) -> None:
        frame = tk.Frame(parent, bg=BTN, padx=14, pady=8)
        frame.pack(fill="x", pady=4)

        tk.Label(frame, text="Mods updated since your last launch:",
                 bg=BTN, fg=MUTED, font=(FONT, 8)).pack(anchor="w", pady=(0, 4))

        txt = tk.Text(frame, bg=BTN, fg=FG, font=(FONT, 9),
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

        self._result = {
            "cancelled": False,
            "ram_gb":   int(self._ram_var.get()),
            "account":  self._account,
            "pending_microsoft": self._result.get("pending_microsoft", False),
        }
        self._root.destroy()

    def _exit(self, root: tk.Tk) -> None:
        self._result = {"cancelled": True}
        root.destroy()
