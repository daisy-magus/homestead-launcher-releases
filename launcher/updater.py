"""
Self-update: compare Gist version against running version, replace binary.

Linux (AppImage): atomic replace + exec (can replace running file on Linux).
Windows: download new installer, run it silently, exit current process.
"""
from __future__ import annotations

import logging
import os
import sys
import tempfile
from pathlib import Path

import requests

from .config import VERSION

logger = logging.getLogger(__name__)


def check_update(server_info: dict) -> str | None:
    """
    Return download URL if Gist reports a newer launcher version, else None.
    Version comparison is simple string comparison — use semver-ordered tags.
    """
    remote = server_info.get("launcher_version", "")
    if not remote or remote <= VERSION:
        return None
    url = server_info.get("windows_url" if sys.platform == "win32" else "linux_url", "")
    logger.info("Update available: %s → %s  (%s)", VERSION, remote, url)
    return url or None


def perform_update(url: str) -> bool:
    """
    Download the new binary and replace the running executable.
    On Linux: replaces AppImage in-place, then exec-restarts the process.
    On Windows: launches new installer silently, exits.
    Returns True only if update succeeded (Linux path exec-replaces, so
    this function actually never returns True on Linux — process is replaced).
    """
    current = Path(sys.executable if getattr(sys, "frozen", False) else sys.argv[0])
    logger.info("Downloading update from %s", url)

    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".new")
    try:
        os.close(tmp_fd)
        tmp = Path(tmp_path)
        with requests.get(url, stream=True, timeout=180) as resp:
            resp.raise_for_status()
            with tmp.open("wb") as fh:
                for chunk in resp.iter_content(64 * 1024):
                    fh.write(chunk)

        if sys.platform != "win32":
            tmp.chmod(0o755)
            tmp.replace(current)
            # Replace this process with the new binary
            os.execv(str(current), sys.argv)
        else:
            import subprocess
            subprocess.Popen([str(tmp), "/VERYSILENT", "/CLOSEAPPLICATIONS"])
            sys.exit(0)

        return True

    except Exception as e:
        logger.error("Update failed: %s", e)
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except Exception:
            pass
        return False
