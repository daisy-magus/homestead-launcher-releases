"""
Linux .desktop entry + icon installation.

Runs once at launch (when frozen binary moves or is installed for the first time).
No-op on Windows or when running from source (unfrozen).
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_ICON_NAME   = "homestead-launcher"
_DESKTOP_ID  = "homestead-launcher.desktop"


def install_desktop_entry() -> None:
    """Install .desktop file and icon into ~/.local/share/. Updates if binary moved."""
    if sys.platform == "win32" or not getattr(sys, "frozen", False):
        return

    exe = Path(sys.executable).resolve()

    desktop_dir = Path.home() / ".local/share/applications"
    icon_dir    = Path.home() / ".local/share/icons/hicolor/256x256/apps"
    desktop_file = desktop_dir / _DESKTOP_ID
    icon_file    = icon_dir / f"{_ICON_NAME}.png"

    # Skip if already installed for this exact binary path
    if desktop_file.exists() and f"Exec={exe}" in desktop_file.read_text():
        return

    png = Path(sys._MEIPASS) / "assets" / "homestead.png"  # type: ignore[attr-defined]
    if not png.exists():
        logger.debug("Icon asset missing — skipping desktop entry install")
        return

    icon_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(png, icon_file)

    desktop_dir.mkdir(parents=True, exist_ok=True)
    desktop_file.write_text(
        "[Desktop Entry]\n"
        "Name=Homestead Launcher\n"
        "Comment=Private Modded Minecraft Server\n"
        f"Exec={exe}\n"
        f"Icon={_ICON_NAME}\n"
        "Type=Application\n"
        "Categories=Game;\n"
        "StartupWMClass=Homestead\n"
        "StartupNotify=true\n"
    )
    logger.info("Installed desktop entry: %s", desktop_file)

    try:
        subprocess.run(
            ["update-desktop-database", str(desktop_dir)],
            capture_output=True, timeout=5,
        )
    except Exception:
        pass
