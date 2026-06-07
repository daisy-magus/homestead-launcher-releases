"""
Homestead Launcher — main entry point.

Launch flow:
  1. Fetch Gist (internet, no Tailscale needed)
  2. Self-update check
  3. Load/refresh saved account
  4. Show pre-launch window (account, RAM, changelog)
  5. Handle Microsoft login if requested
  6. Progress window + background thread:
       LAN probe → Tailscale (if needed) → mod sync → MC install → Forge install → launch
  7. Stream MC logs → detect "Sound engine started" → hide window
  8. Wait for exit → crash check → show error if crashed
  9. tailscale down (if we connected it)
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import tkinter as tk
import tkinter.messagebox as mb
from pathlib import Path

from . import auth, install, minecraft, network, sync, updater
from .config import (
    GIST_URL,
    auth_file, instance_dir, manifest_cache_file, crash_log_file,
)
from .ui.prelaunch import PreLaunchWindow
from .ui.progress import ProgressWindow

logger = logging.getLogger("homestead")

READY_SIGNAL = "Sound engine started"


# ── Helpers ────────────────────────────────────────────────────────────────────

def _fix_openal() -> None:
    """Force OpenAL soft backend on Linux to avoid ALSA / PulseAudio issues."""
    if sys.platform == "win32":
        return
    p = Path.home() / ".alsoftrc"
    content = p.read_text() if p.exists() else ""
    if "drivers=soft" not in content:
        p.write_text(content.rstrip() + "\ndrivers=soft\n")


def _read_crash_info(mc_dir: Path) -> str:
    crash_dir = mc_dir / "crash-reports"
    if crash_dir.is_dir():
        reports = sorted(crash_dir.glob("*.txt"), reverse=True)
        if reports:
            text = reports[0].read_text(errors="replace")
            return text[:4000] + ("\n…truncated" if len(text) > 4000 else "")
    log = mc_dir / "logs" / "latest.log"
    if log.exists():
        lines = log.read_text(errors="replace").splitlines()
        return "[No crash report — last 60 lines of latest.log]\n\n" + "\n".join(lines[-60:])
    return "No crash report or log found."


def _msgbox_error(title: str, msg: str) -> None:
    root = tk.Tk()
    root.withdraw()
    mb.showerror(title, msg)
    root.destroy()


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> int:
    logging.basicConfig(
        level=logging.DEBUG if ("--verbose" in sys.argv or "-v" in sys.argv) else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    if "--logout" in sys.argv:
        p = auth_file()
        if p.exists():
            p.unlink()
            print("Logged out.")
        return 0

    _fix_openal()
    install.install_desktop_entry()

    # ── 1. Fetch Gist ──────────────────────────────────────────────────────────
    server_info: dict = {}
    try:
        server_info = sync.fetch_server_info(GIST_URL)
    except Exception as e:
        logger.warning("Could not fetch server info: %s", e)

    tailscale_key = server_info.get("tailscale_key", "")
    lan_ip        = server_info.get("lan_ip",    "192.168.1.52")
    game_ip       = server_info.get("game_ip",   lan_ip)
    game_port     = int(server_info.get("game_port", 25565))
    sync_url      = server_info.get("sync_url",  "")

    # ── 2. Self-update ─────────────────────────────────────────────────────────
    update_url = updater.check_update(server_info)
    if update_url:
        updater.perform_update(update_url)
        return 0  # only reached on update failure

    # ── 3. Load saved account ──────────────────────────────────────────────────
    saved_account = auth.load_account(auth_file())

    # ── 4. Changelog ───────────────────────────────────────────────────────────
    last_manifest = sync.load_last_manifest(manifest_cache_file())
    changelog = sync.get_changelog(server_info, last_manifest) if last_manifest else None

    # ── 5. Pre-launch window ───────────────────────────────────────────────────
    pre = PreLaunchWindow(saved_account=saved_account, changelog=changelog)
    result = pre.run()

    if result.get("cancelled"):
        return 130

    ram_gb  = result["ram_gb"]
    account = result.get("account")
    jvm_args = [f"-Xmx{ram_gb}G", f"-Xms{max(1, ram_gb // 2)}G"]

    # ── 6. Microsoft login (if requested from pre-launch window) ───────────────
    if result.get("pending_microsoft"):
        try:
            account = auth.login_microsoft()
            auth.save_account(auth_file(), account)
        except Exception as e:
            _msgbox_error("Login failed", str(e))
            return 1

    if account is None:
        _msgbox_error("No account", "Please sign in before launching.")
        return 1

    # ── 7. Progress window + background work ───────────────────────────────────
    exit_code = [0]
    tailscale_used = [False]
    mc_dir = instance_dir()

    def work(win: ProgressWindow) -> None:

        # Network ──────────────────────────────────────────────────────────────
        win.update("Checking network…", "")
        if network.is_lan_reachable(lan_ip):
            host, port = lan_ip, game_port
            logger.info("LAN reachable — bypassing Tailscale")
        else:
            win.update("Connecting to Homestead network…", "")
            if not tailscale_key:
                win.show_error(
                    "Cannot connect",
                    "No server info or Tailscale key available.\n"
                    "Check your internet connection and try again.",
                )
                exit_code[0] = 1
                return
            if not network.ensure_connected(tailscale_key):
                win.show_error(
                    "Could not connect to Homestead network",
                    "Make sure you have an internet connection.\n\n"
                    "If this keeps happening, ask Darcy to rotate the network key.",
                )
                exit_code[0] = 1
                return
            tailscale_used[0] = True
            host, port = game_ip, game_port

        # Mod sync ─────────────────────────────────────────────────────────────
        if sync_url:
            win.update("Checking for mod updates…", "")
            try:
                manifest = sync.fetch_manifest(sync_url)

                def on_sync(label: str, cur: int, total: int) -> None:
                    name = label.split("/")[-1] if "/" in label else label
                    win.update("Syncing mods…", name)
                    win.set_progress(cur, max(total, 1))

                stats = sync.sync(
                    server_url=sync_url,
                    manifest=manifest,
                    instance_dir=mc_dir,
                    progress=on_sync,
                )
                sync.save_last_manifest(manifest_cache_file(), manifest)
                logger.info(
                    "Sync done: %d downloaded, %d removed, %d unchanged",
                    stats.downloaded, stats.removed, stats.unchanged,
                )
                win.indeterminate()
            except Exception as e:
                logger.warning("Mod sync failed: %s", e)
                win.update("Mod sync skipped", f"Using existing files  ({e})")
                win.indeterminate()

        # Minecraft install ─────────────────────────────────────────────────────
        win.update("Checking Minecraft install…", "First run may take several minutes")
        try:
            def on_mc(label: str, cur: int, total: int) -> None:
                win.update("Installing Minecraft…", label)
                if total > 1:
                    win.set_progress(cur, total)

            minecraft.install_minecraft(mc_dir, progress=on_mc)
            win.indeterminate()
        except Exception:
            import traceback
            win.show_error("Minecraft install failed", traceback.format_exc())
            exit_code[0] = 1
            return

        # Forge install ─────────────────────────────────────────────────────────
        win.update("Checking Forge install…", "")
        try:
            def on_forge(label: str, cur: int, total: int) -> None:
                win.update("Installing Forge…", label)
                if total > 1:
                    win.set_progress(cur, total)

            minecraft.install_forge(mc_dir, progress=on_forge)
            win.indeterminate()
        except Exception:
            import traceback
            win.show_error("Forge install failed", traceback.format_exc())
            exit_code[0] = 1
            return

        # Launch ────────────────────────────────────────────────────────────────
        win.update("Launching Minecraft…", f"Connecting to {host}:{port}")
        try:
            proc = minecraft.launch(
                mc_dir=mc_dir,
                account=account,
                server_host=host,
                server_port=port,
                jvm_args=jvm_args,
            )
        except Exception:
            import traceback
            win.show_error("Launch failed", traceback.format_exc())
            exit_code[0] = 1
            return

        win.show_console()

        # Stream logs — hide window once Minecraft's main menu loads
        ready = [False]

        def stream_logs() -> None:
            for line in proc.stdout:
                stripped = line.rstrip()
                win.log_line(stripped)
                if not ready[0] and READY_SIGNAL in stripped:
                    ready[0] = True
                    win.hide()

        log_thread = threading.Thread(target=stream_logs, daemon=True)
        log_thread.start()

        proc.wait()
        log_thread.join(timeout=2)

        if tailscale_used[0]:
            network.tailscale_down()

        if proc.returncode != 0:
            win.show()
            win.show_error(
                f"Minecraft crashed  (exit {proc.returncode})",
                _read_crash_info(mc_dir),
            )
            exit_code[0] = 1
            return

        win.close()

    ProgressWindow.run_with(work)
    return exit_code[0]


if __name__ == "__main__":
    sys.exit(main())
