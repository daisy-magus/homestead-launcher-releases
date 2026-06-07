"""
Network helpers: LAN probe, Tailscale connect/disconnect, Tailscale install.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from .config import LAN_IP, TAILSCALE_IP_PREFIX

logger = logging.getLogger(__name__)


# ── LAN probe ──────────────────────────────────────────────────────────────────

def is_lan_reachable(host: str = LAN_IP, timeout: float = 1.5) -> bool:
    """Ping host once with short timeout. Returns True if reachable."""
    if sys.platform == "win32":
        # -w is milliseconds on Windows
        cmd = ["ping", "-n", "1", "-w", str(int(timeout * 1000)), host]
    else:
        cmd = ["ping", "-c", "1", "-W", str(int(timeout)), host]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout + 2)
        return r.returncode == 0
    except Exception:
        return False


# ── Tailscale helpers ──────────────────────────────────────────────────────────

def _ts(*args: str, timeout: float = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["tailscale", *args],
        capture_output=True, text=True, timeout=timeout,
    )


def tailscale_installed() -> bool:
    return shutil.which("tailscale") is not None


def tailscale_connected() -> bool:
    try:
        r = _ts("ip", "--4", timeout=5)
        ip = r.stdout.strip()
        return r.returncode == 0 and ip.startswith(TAILSCALE_IP_PREFIX)
    except Exception:
        return False


def tailscale_up(auth_key: str) -> bool:
    """Connect using auth_key. Returns True if connected afterwards."""
    try:
        r = _ts("up", f"--authkey={auth_key}", "--hostname=better-player", timeout=60)
        if r.returncode != 0:
            logger.warning("tailscale up failed: %s", (r.stderr or r.stdout).strip())
            return False
        return tailscale_connected()
    except subprocess.TimeoutExpired:
        logger.warning("tailscale up timed out")
        return False
    except Exception as e:
        logger.warning("tailscale up error: %s", e)
        return False


def tailscale_down() -> None:
    """Disconnect. Best-effort — never raises."""
    try:
        _ts("down", timeout=15)
        logger.info("tailscale down OK")
    except Exception as e:
        logger.debug("tailscale down: %s", e)


def ensure_connected(auth_key: str) -> bool:
    """
    Connect to Tailscale, installing it first if absent.
    Returns True if connected.
    """
    if tailscale_connected():
        return True
    if not tailscale_installed():
        if not _install_tailscale():
            return False
    return tailscale_up(auth_key)


# ── Tailscale install ──────────────────────────────────────────────────────────

def _install_tailscale() -> bool:
    if sys.platform == "win32":
        return _install_tailscale_windows()
    elif sys.platform.startswith("linux"):
        return _install_tailscale_linux()
    logger.error("Unsupported platform for Tailscale auto-install: %s", sys.platform)
    return False


def _install_tailscale_linux() -> bool:
    """
    Download install.sh + grant operator rights in a single pkexec call
    so the user sees exactly one password prompt.
    """
    user = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
    install_path = wrapper_path = ""
    try:
        with urllib.request.urlopen("https://tailscale.com/install.sh", timeout=30) as resp:
            install_script = resp.read()

        with tempfile.NamedTemporaryFile(suffix="_ts_install.sh", delete=False) as f:
            f.write(install_script)
            install_path = f.name
        os.chmod(install_path, 0o700)

        operator_line = f"tailscale set --operator={user}\n" if user else ""
        wrapper = f"#!/bin/sh\nsh '{install_path}'\n{operator_line}"
        with tempfile.NamedTemporaryFile(suffix="_ts_wrapper.sh", delete=False) as f:
            f.write(wrapper.encode())
            wrapper_path = f.name
        os.chmod(wrapper_path, 0o700)

        elevator = "pkexec" if shutil.which("pkexec") else "sudo"
        result = subprocess.run([elevator, "sh", wrapper_path], timeout=180)
        return result.returncode == 0

    except Exception as e:
        logger.error("Tailscale Linux install failed: %s", e)
        return False
    finally:
        for p in [install_path, wrapper_path]:
            if p:
                try:
                    os.unlink(p)
                except OSError:
                    pass


def _install_tailscale_windows() -> bool:
    """Download Tailscale installer, elevate via UAC (ShellExecute runas)."""
    import ctypes
    url = "https://pkgs.tailscale.com/stable/tailscale-setup-latest.exe"
    tmp = Path(os.environ.get("TEMP", "C:\\Temp")) / "tailscale-setup.exe"
    try:
        urllib.request.urlretrieve(url, tmp)
        ret = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", str(tmp), "/install /quiet /norestart", None, 1
        )
        if ret <= 32:
            logger.error("UAC elevation failed (ShellExecute returned %d)", ret)
            return False
        for _ in range(30):
            time.sleep(2)
            if shutil.which("tailscale"):
                return True
        logger.error("Tailscale install timed out")
        return False
    except Exception as e:
        logger.error("Tailscale Windows install failed: %s", e)
        return False
