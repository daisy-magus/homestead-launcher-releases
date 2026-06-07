"""
Linux binary self-install + .desktop entry + icon installation.

On first run from any location (e.g. ~/Downloads), the binary copies
itself to ~/.local/bin/BetterLauncher and exec-restarts from there. The
.desktop file always points to that stable path. No-op on Windows or
when running from source (unfrozen).
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_INSTALL_PATH = Path.home() / ".local/bin/BetterLauncher"
_ICON_NAME    = "better-launcher"
_DESKTOP_ID   = "better-launcher.desktop"


def install_binary() -> None:
    """
    Copy binary to ~/.local/bin/BetterLauncher if not already running from there,
    then exec-restart. Never returns if a copy was needed.
    """
    if sys.platform == "win32" or not getattr(sys, "frozen", False):
        return

    current = Path(sys.executable).resolve()
    target  = _INSTALL_PATH.resolve()

    if current == target:
        return

    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(current, target)
    target.chmod(0o755)
    logger.info("Installed binary to %s — restarting", target)
    os.execv(str(target), [str(target)] + sys.argv[1:])


def install_desktop_entry() -> None:
    """Install .desktop file and icon into ~/.local/share/. Updates if needed."""
    if sys.platform == "win32" or not getattr(sys, "frozen", False):
        return

    # Always point the .desktop at the stable install path, not sys.executable
    exe = _INSTALL_PATH

    desktop_dir  = Path.home() / ".local/share/applications"
    icon_dir     = Path.home() / ".local/share/icons/hicolor/256x256/apps"
    desktop_file = desktop_dir / _DESKTOP_ID

    if desktop_file.exists() and f"Exec={exe}" in desktop_file.read_text():
        return

    png = Path(sys._MEIPASS) / "assets" / "homestead.png"  # type: ignore[attr-defined]
    if not png.exists():
        logger.debug("Icon asset missing — skipping desktop entry install")
        return

    icon_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(png, icon_dir / f"{_ICON_NAME}.png")

    desktop_dir.mkdir(parents=True, exist_ok=True)
    desktop_file.write_text(
        "[Desktop Entry]\n"
        "Name=Better Launcher\n"
        "Comment=Better Private Modded Minecraft Server\n"
        f"Exec={exe}\n"
        f"Icon={_ICON_NAME}\n"
        "Type=Application\n"
        "Categories=Game;\n"
        "StartupWMClass=BetterLauncher\n"
        "StartupNotify=true\n"
    )
    logger.info("Installed desktop entry: %s", desktop_file)

    for cmd in (
        ["update-desktop-database", str(desktop_dir)],
        ["gtk-update-icon-cache", "-f", "-t", str(Path.home() / ".local/share/icons/hicolor")],
    ):
        try:
            subprocess.run(cmd, capture_output=True, timeout=5)
        except Exception:
            pass
