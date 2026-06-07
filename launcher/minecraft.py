"""
Minecraft + Forge install and launch.

Key design decisions:
- Forge installer is downloaded as a JAR from Maven (not files.minecraftforge.net)
  and run directly — avoids minecraft_launcher_lib's temp-file race condition.
- The Mojang-distributed JRE (java-runtime-gamma, Java 17.0.x) is used for BOTH
  Forge install and game launch so the remapping step produces consistent byte output
  across all machines (different Java versions produce byte-different but functionally
  identical output, causing checksum failures).
- servers.dat is written from raw NBT bytes — no nbt library dependency.
"""
from __future__ import annotations

import json
import logging
import struct
import subprocess
import sys
from pathlib import Path
from typing import Callable

import requests
from minecraft_launcher_lib import command as mc_command
from minecraft_launcher_lib import install as mc_install

from .auth import MinecraftAccount
from .config import (
    MC_VERSION, FORGE_VERSION, FORGE_MAVEN_URL,
    instance_dir as _instance_dir, forge_installer_cache,
)

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str, int, int], None]

_JRE_COMPONENT = "java-runtime-gamma"


# ── Java discovery ─────────────────────────────────────────────────────────────

def find_mojang_java(mc_dir: Path) -> str:
    """
    Return path to the JRE that mc_install downloaded into mc_dir/runtime/.
    Falls back to 'java' if not found (first-run before install completes).
    """
    runtime_root = mc_dir / "runtime"
    if not runtime_root.is_dir():
        return "java"

    exe = "java.exe" if sys.platform == "win32" else "java"

    if sys.platform == "win32":
        platform_dirs = ["windows-x64", "windows"]
    elif sys.platform == "darwin":
        platform_dirs = ["mac-os", "mac-os-arm64"]
    else:
        platform_dirs = ["linux", "linux-i386"]

    for platform_dir in platform_dirs:
        candidate = (
            runtime_root / _JRE_COMPONENT / platform_dir / _JRE_COMPONENT / "bin" / exe
        )
        if candidate.exists():
            logger.debug("Mojang JRE: %s", candidate)
            return str(candidate)

    # Fallback: any java binary under runtime/
    for candidate in runtime_root.rglob(exe):
        if "bin" in candidate.parts:
            logger.debug("Mojang JRE (fallback): %s", candidate)
            return str(candidate)

    return "java"


# ── Install checks ─────────────────────────────────────────────────────────────

def is_mc_installed(mc_dir: Path) -> bool:
    return (mc_dir / "versions" / MC_VERSION).is_dir()


def is_forge_installed(mc_dir: Path) -> bool:
    forge_short = FORGE_VERSION.split("-", 1)[-1]  # "47.4.10"
    versions_dir = mc_dir / "versions"
    if not versions_dir.is_dir():
        return False
    return any(
        d.is_dir() and "forge" in d.name.lower() and forge_short in d.name
        for d in versions_dir.iterdir()
    )


def find_forge_version_name(mc_dir: Path) -> str:
    forge_short = FORGE_VERSION.split("-", 1)[-1]
    for d in sorted((mc_dir / "versions").iterdir()):
        if d.is_dir() and "forge" in d.name.lower() and forge_short in d.name:
            return d.name
    raise RuntimeError(f"Forge install not found in {mc_dir / 'versions'}")


# ── Install ────────────────────────────────────────────────────────────────────

def install_minecraft(mc_dir: Path, progress: ProgressCallback | None = None) -> None:
    if is_mc_installed(mc_dir):
        logger.info("Minecraft %s already installed", MC_VERSION)
        return
    logger.info("Installing Minecraft %s", MC_VERSION)

    cur = [0]
    total = [1]

    def _status(s: str) -> None:
        if progress:
            progress(s, cur[0], total[0])

    def _set_progress(p: int) -> None:
        cur[0] = p
        if progress:
            progress("", cur[0], total[0])

    def _set_max(m: int) -> None:
        total[0] = max(1, m)

    mc_install.install_minecraft_version(
        version=MC_VERSION,
        minecraft_directory=str(mc_dir),
        callback={"setStatus": _status, "setProgress": _set_progress, "setMax": _set_max},
    )


def _download_forge_jar(dest: Path, progress: ProgressCallback | None = None) -> None:
    if dest.exists():
        logger.debug("Forge JAR cached at %s", dest)
        return
    logger.info("Downloading Forge installer from Maven")
    if progress:
        progress("Downloading Forge installer…", 0, 1)

    headers = {
        "User-Agent": "HomesteadLauncher/1.0",
        "Referer":    "https://files.minecraftforge.net/",
    }
    tmp = dest.with_suffix(".part")
    with requests.get(FORGE_MAVEN_URL, headers=headers, stream=True, timeout=120) as resp:
        resp.raise_for_status()
        total = int(resp.headers.get("content-length", 0))
        done = 0
        with tmp.open("wb") as fh:
            for chunk in resp.iter_content(64 * 1024):
                fh.write(chunk)
                done += len(chunk)
                if progress and total:
                    progress("Downloading Forge installer…", done, total)
    tmp.replace(dest)
    logger.info("Forge JAR saved to %s", dest)


def install_forge(mc_dir: Path, progress: ProgressCallback | None = None) -> None:
    if is_forge_installed(mc_dir):
        logger.info("Forge %s already installed", FORGE_VERSION)
        return

    # Must run after install_minecraft so java-runtime-gamma is present
    java = find_mojang_java(mc_dir)
    logger.info("Using Java for Forge install: %s", java)

    jar = forge_installer_cache()
    _download_forge_jar(jar, progress)

    if progress:
        progress("Installing Forge…", 0, 1)

    # Forge installer requires launcher_profiles.json to exist
    profiles_file = mc_dir / "launcher_profiles.json"
    if not profiles_file.exists():
        profiles_file.write_text(json.dumps({"profiles": {}, "selectedProfile": "", "clientToken": "", "authenticationDatabase": {}}))

    logger.info("Running: %s -jar %s --installClient %s", java, jar, mc_dir)
    result = subprocess.run(
        [java, "-jar", str(jar), "--installClient", str(mc_dir)],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if result.returncode != 0:
        output = (result.stdout + "\n" + result.stderr).strip()
        raise RuntimeError(
            f"Forge installer failed (exit {result.returncode}):\n{output}"
        )
    if not is_forge_installed(mc_dir):
        raise RuntimeError(
            "Forge installer exited 0 but Forge version not found in versions/"
        )
    logger.info("Forge %s installed", FORGE_VERSION)


# ── servers.dat ───────────────────────────────────────────────────────────────

def write_servers_dat(mc_dir: Path, name: str, host: str, port: int) -> None:
    """
    Write servers.dat from scratch using raw NBT bytes.
    No nbt library required.
    """
    address = f"{host}:{port}"

    def nbt_str(s: str) -> bytes:
        b = s.encode("utf-8")
        return struct.pack(">H", len(b)) + b

    def compound(entries: list[tuple[int, str, bytes]]) -> bytes:
        data = b""
        for tag_id, tag_name, payload in entries:
            data += bytes([tag_id]) + nbt_str(tag_name) + payload
        return data + b"\x00"  # TAG_End

    server_entry = compound([
        (8, "ip",             nbt_str(address)),
        (8, "name",           nbt_str(name)),
        (1, "acceptTextures", bytes([1])),
    ])

    # TAG_List of TAG_Compound: type=10, count=1
    servers_list = struct.pack(">bi", 10, 1) + server_entry

    root_payload = compound([(9, "servers", servers_list)])
    nbt = bytes([10]) + nbt_str("") + root_payload

    (mc_dir / "servers.dat").write_bytes(nbt)
    logger.debug("servers.dat: %s → %s", name, address)


# ── Launch ────────────────────────────────────────────────────────────────────

def launch(
    mc_dir: Path,
    account: MinecraftAccount,
    server_host: str,
    server_port: int,
    jvm_args: list[str] | None = None,
) -> subprocess.Popen:
    """Launch Minecraft and return the Popen handle."""
    version_name = find_forge_version_name(mc_dir)
    write_servers_dat(mc_dir, "Homestead", server_host, server_port)

    options = {
        "username":      account.username,
        "uuid":          account.uuid,
        "token":         account.access_token,
        "gameDirectory": str(mc_dir),
        "jvmArguments":  (jvm_args or []) + ["-XX:+UseG1GC"],
        "server":        server_host,
        "port":          str(server_port),
    }

    # get_minecraft_command already sets cmd[0] to the Mojang JRE path.
    # We do NOT override it — that JRE is the same one used for Forge install,
    # ensuring consistent remapping output and no AWT/X11 path issues.
    cmd = mc_command.get_minecraft_command(
        version=version_name,
        minecraft_directory=str(mc_dir),
        options=options,
    )

    logger.info("Launching %s", version_name)
    return subprocess.Popen(
        cmd,
        cwd=str(mc_dir),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
        errors="replace",
    )
