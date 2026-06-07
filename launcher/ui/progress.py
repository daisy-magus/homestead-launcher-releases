"""
Progress, console, and error window.

All public methods are thread-safe — call from the background work thread.
Main thread runs the tkinter event loop via run_with().

States:
  progress  — spinning bar + status/detail text (install, sync, connect)
  console   — live scrolling Minecraft log
  error     — scrollable error detail + Close button (blocks work thread until dismissed)

Additional control:
  hide()    — withdraw window when Minecraft is ready (Sound engine started)
  show()    — deiconify on crash
"""
from __future__ import annotations

import queue
import sys
import threading
import tkinter as tk
from tkinter import ttk
from typing import Callable

from .widgets import BG, FG, MUTED, BLUE, BTN, RED, FONT, button, center


class ProgressWindow:

    def __init__(self) -> None:
        self._q: queue.Queue = queue.Queue()
        self._root: tk.Tk | None = None
        self._content: tk.Frame | None = None
        self._status_var: tk.StringVar | None = None
        self._detail_var: tk.StringVar | None = None
        self._bar: ttk.Progressbar | None = None
        self._console_txt: tk.Text | None = None
        self._unblock = threading.Event()

    # ── Thread-safe API ────────────────────────────────────────────────────────

    def update(self, status: str, detail: str = "") -> None:
        self._q.put(("update", status, detail))

    def set_progress(self, value: float, maximum: float) -> None:
        self._q.put(("progress", value, maximum))

    def indeterminate(self) -> None:
        self._q.put(("indeterminate",))

    def show_console(self) -> None:
        self._q.put(("console",))

    def log_line(self, text: str) -> None:
        self._q.put(("log_line", text))

    def hide(self) -> None:
        """Hide window after Minecraft's main menu loads."""
        self._q.put(("hide",))

    def show(self) -> None:
        """Re-show window (e.g. on crash)."""
        self._q.put(("show",))

    def show_error(self, title: str, detail: str = "") -> None:
        """Display error screen. Blocks the work thread until user clicks Close."""
        self._unblock.clear()
        self._q.put(("error", title, detail))
        self._unblock.wait()

    def close(self) -> None:
        self._q.put(("close",))

    # ── Build ──────────────────────────────────────────────────────────────────

    def _build(self) -> None:
        root = tk.Tk()
        root.title("Homestead Launcher")
        root.resizable(False, False)
        root.configure(bg=BG)
        root.protocol("WM_DELETE_WINDOW", lambda: sys.exit(0))
        self._root = root
        center(root, 400, 190)

        tk.Label(root, text="Homestead", bg=BG, fg=BLUE,
                 font=(FONT, 14, "bold")).pack(pady=(18, 0))
        self._content = tk.Frame(root, bg=BG)
        self._content.pack(fill="both", expand=True)
        self._render_progress()

    def _clear(self) -> None:
        for w in self._content.winfo_children():
            w.destroy()
        self._status_var = None
        self._detail_var = None
        self._bar = None
        self._console_txt = None

    # ── Render states ──────────────────────────────────────────────────────────

    def _render_progress(self) -> None:
        self._clear()
        f = self._content

        self._status_var = tk.StringVar(value="Starting…")
        tk.Label(f, textvariable=self._status_var,
                 bg=BG, fg=FG, font=(FONT, 11, "bold")).pack(pady=(12, 2))

        self._detail_var = tk.StringVar(value="")
        tk.Label(f, textvariable=self._detail_var,
                 bg=BG, fg=MUTED, font=(FONT, 9)).pack()

        style = ttk.Style()
        style.theme_use("default")
        style.configure("H.Horizontal.TProgressbar",
                        troughcolor=BTN, background=BLUE,
                        borderwidth=0, thickness=6)
        self._bar = ttk.Progressbar(
            f, style="H.Horizontal.TProgressbar",
            orient="horizontal", length=320, mode="indeterminate",
        )
        self._bar.pack(pady=16)
        self._bar.start(12)

    def _render_console(self) -> None:
        self._clear()
        center(self._root, 640, 420)
        f = self._content

        tk.Label(f, text="Minecraft is running  —  close this window anytime",
                 bg=BG, fg=MUTED, font=(FONT, 8)).pack(pady=(6, 2))

        outer = tk.Frame(f, bg=BTN)
        outer.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        txt = tk.Text(outer, bg="#0d0d1a", fg="#c8c8d4",
                      font=("Courier New", 8), relief="flat",
                      wrap="none", state="disabled", padx=6, pady=4)
        ysb = tk.Scrollbar(outer, command=txt.yview,
                           bg=BTN, troughcolor=BTN, relief="flat", width=8)
        xsb = tk.Scrollbar(outer, orient="horizontal", command=txt.xview,
                           bg=BTN, troughcolor=BTN, relief="flat", width=8)
        txt.config(yscrollcommand=ysb.set, xscrollcommand=xsb.set)
        ysb.pack(side="right", fill="y")
        xsb.pack(side="bottom", fill="x")
        txt.pack(fill="both", expand=True)
        self._console_txt = txt

    def _render_error(self, title: str, detail: str) -> None:
        self._clear()
        center(self._root, 480, 340)
        f = self._content

        tk.Label(f, text=f"✗  {title}",
                 bg=BG, fg=RED, font=(FONT, 11, "bold")).pack(pady=(12, 6))

        if detail:
            outer = tk.Frame(f, bg=BTN)
            outer.pack(fill="x", padx=16, pady=(0, 4))
            txt = tk.Text(outer, bg=BTN, fg=MUTED, font=("Courier", 8),
                          height=10, relief="flat", wrap="word", padx=8, pady=6)
            txt.insert("1.0", detail)
            txt.config(state="disabled")
            sb = tk.Scrollbar(outer, command=txt.yview,
                              bg=BTN, troughcolor=BTN, relief="flat", width=8)
            txt.config(yscrollcommand=sb.set)
            sb.pack(side="right", fill="y")
            txt.pack(side="left", fill="both", expand=True)

        def dismiss() -> None:
            self._root.destroy()
            self._unblock.set()

        button(f, "Close", dismiss).pack(pady=10)

    # ── Poll loop ──────────────────────────────────────────────────────────────

    def _poll(self) -> None:
        try:
            while True:
                msg = self._q.get_nowait()
                kind = msg[0]

                if kind == "update":
                    if self._status_var:
                        self._status_var.set(msg[1])
                    if self._detail_var:
                        self._detail_var.set(msg[2])

                elif kind == "progress":
                    if self._bar:
                        self._bar.stop()
                        self._bar.config(mode="determinate",
                                         maximum=max(msg[2], 1), value=msg[1])

                elif kind == "indeterminate":
                    if self._bar:
                        self._bar.config(mode="indeterminate")
                        self._bar.start(12)

                elif kind == "console":
                    self._render_console()

                elif kind == "log_line":
                    if self._console_txt:
                        t = self._console_txt
                        t.config(state="normal")
                        t.insert("end", msg[1] + "\n")
                        t.see("end")
                        t.config(state="disabled")

                elif kind == "hide":
                    self._root.withdraw()

                elif kind == "show":
                    self._root.deiconify()

                elif kind == "error":
                    self._render_error(msg[1], msg[2])
                    return  # dismiss() resumes via _unblock; window destroys itself

                elif kind == "close":
                    self._root.destroy()
                    return

        except queue.Empty:
            pass

        self._root.after(80, self._poll)

    # ── Entry point ────────────────────────────────────────────────────────────

    @classmethod
    def run_with(cls, target: Callable[["ProgressWindow"], None]) -> None:
        """
        Show the progress window, run target(window) in a daemon thread.
        Blocks via tkinter mainloop until the window is closed.
        """
        win = cls()
        win._build()

        def thread_body() -> None:
            try:
                target(win)
            except SystemExit:
                win.close()
            except BaseException:
                import traceback
                tb = traceback.format_exc()
                _write_crash_log(tb)
                win.show_error("Unexpected crash", tb)

        t = threading.Thread(target=thread_body, daemon=True)
        t.start()
        win._root.after(80, win._poll)
        win._root.mainloop()
        t.join(timeout=2)


def _write_crash_log(text: str) -> None:
    try:
        from ..config import crash_log_file
        crash_log_file().write_text(text)
    except Exception:
        pass
