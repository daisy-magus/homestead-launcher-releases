"""
Central configuration and path helpers.
All file-system paths funnelled through here so nothing else imports platformdirs.
"""
from __future__ import annotations

import sys
from pathlib import Path

from platformdirs import user_config_dir, user_data_dir

VERSION = "1.10.0"

APP_NAME   = "HomesteadLauncher"
APP_AUTHOR = "Homestead"

MC_VERSION    = "1.20.1"
FORGE_VERSION = "1.20.1-47.4.10"

# Maven repo — no Referer gate unlike files.minecraftforge.net
FORGE_MAVEN_URL = (
    "https://maven.minecraftforge.net/net/minecraftforge/forge/"
    f"{FORGE_VERSION}/forge-{FORGE_VERSION}-installer.jar"
)

# GitHub Gist — single source of truth for server info + launcher updates + Tailscale key
GIST_URL = (
    "https://gist.githubusercontent.com/daisy-magus/"
    "7a8201ed67cc3097e0430d1c9df038ab/raw/homestead-server.json"
)

# Prism Launcher's public OAuth client ID — no Azure registration needed
OAUTH_CLIENT_ID = "c36a9fb6-4f2a-41ff-90bd-ae7cc92031eb"
OAUTH_PORT      = 18272
OAUTH_REDIRECT  = f"http://localhost:{OAUTH_PORT}/"

# LAN probe target — skip Tailscale when on the home network
LAN_IP = "192.168.1.52"

TAILSCALE_IP_PREFIX = "100."


# ── Path helpers ───────────────────────────────────────────────────────────────

def _config_dir() -> Path:
    p = Path(user_config_dir(APP_NAME, APP_AUTHOR, roaming=True))
    p.mkdir(parents=True, exist_ok=True)
    return p


def _data_dir() -> Path:
    p = Path(user_data_dir(APP_NAME, APP_AUTHOR, roaming=True))
    p.mkdir(parents=True, exist_ok=True)
    return p


def auth_file() -> Path:
    return _config_dir() / "auth.json"


def instance_dir() -> Path:
    """Isolated Minecraft game directory."""
    p = _data_dir() / "instance"
    p.mkdir(parents=True, exist_ok=True)
    return p


def manifest_cache_file() -> Path:
    return _data_dir() / "last_manifest.json"


def forge_installer_cache() -> Path:
    """Stable location for the downloaded Forge installer JAR."""
    p = _data_dir() / "cache"
    p.mkdir(parents=True, exist_ok=True)
    return p / f"forge-{FORGE_VERSION}-installer.jar"


def crash_log_file() -> Path:
    return _data_dir() / "crash.log"
