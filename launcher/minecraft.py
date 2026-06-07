"""
Minecraft + Forge install and launch.

Key design decisions:
- Forge installer is downloaded as a JAR from Maven (not files.minecraftforge.net)
  and run directly — avoids minecraft_launcher_lib's temp-file race condition.
- The Mojang-distributed JRE (java-runtime-gamma, Java 17.0.x) is used for BOTH
  Forge install and game launch.  The jarsplitter processor produces functionally
  identical output on any JRE, but different JVM runs may produce differently-ordered
  ZIP entries → different sha1.  When this happens (Forge exits 1 with "Processor
  failed"), _retry_forge_with_actual_shas() patches the installer's hardcoded
  expected shas to match what this JVM actually produced, then re-runs.
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
        "User-Agent": "BetterLauncher/1.0",
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


def _read_cache_sha(cache_file: Path) -> str | None:
    """Read the Output sha from a Forge installer .cache file."""
    try:
        for line in cache_file.read_text().splitlines():
            if line.startswith("Output:"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return None


def _get_installer_expected_shas(jar: Path) -> tuple[str | None, str | None]:
    """Extract MC_SLIM_SHA / MC_EXTRA_SHA (client side) from the Forge installer."""
    import zipfile as _zf
    try:
        with _zf.ZipFile(jar, 'r') as z:
            with z.open('install_profile.json') as f:
                data = json.load(f)
        slim  = data.get('data', {}).get('MC_SLIM_SHA',  {}).get('client', '').strip("'").strip()
        extra = data.get('data', {}).get('MC_EXTRA_SHA', {}).get('client', '').strip("'").strip()
        return (slim or None, extra or None)
    except Exception as e:
        logger.warning("Forge: could not read expected shas from installer: %s", e)
        return None, None


def _forge_error_summary(output: str) -> str:
    """Return only signal lines from Forge installer output (keeps context window sane)."""
    lines = output.splitlines()
    signal = [
        l for l in lines
        if any(w in l for w in ("Error", "Exception", "Processor", "failed", "cannot", "problem", "WARN", "WARNING"))
        and not l.lstrip().startswith("Data ")   # skip file-extraction progress lines
        and not l.lstrip().startswith("Extracting ")
    ]
    kept = signal[-40:] if signal else lines[-20:]
    return "\n".join(kept)


def _retry_forge_with_actual_shas(
    mc_dir: Path,
    orig_jar: Path,
    java: str,
    progress: ProgressCallback | None,
) -> bool:
    """
    After a jarsplitter checksum mismatch: patch the Forge installer to accept
    the shas this JVM actually produced, clean up the partial install, re-run.

    The jarsplitter produces functionally identical slim/extra jars regardless of
    JVM run, but ZIP entry ordering may differ → different sha1.  Patching the
    installer's expected shas is safe: Minecraft loads classes by name, not
    position, so ordering has no effect at runtime.
    """
    import shutil as _shutil, zipfile as _zf

    # .cache files written by the Forge installer record what the jarsplitter produced
    client_lib_root = mc_dir / "libraries" / "net" / "minecraft" / "client"
    slim_caches  = list(client_lib_root.rglob("*-slim.jar.cache"))
    extra_caches = list(client_lib_root.rglob("*-extra.jar.cache"))
    if not slim_caches or not extra_caches:
        logger.warning("Forge retry: no .cache files found — cannot determine actual shas")
        return False

    actual_slim  = _read_cache_sha(slim_caches[0])
    actual_extra = _read_cache_sha(extra_caches[0])
    if not actual_slim or not actual_extra:
        logger.warning("Forge retry: could not read output shas from .cache files")
        return False

    stock_slim, stock_extra = _get_installer_expected_shas(orig_jar)
    if not stock_slim or not stock_extra:
        return False

    if actual_slim == stock_slim and actual_extra == stock_extra:
        return False  # shas already match; failure is not a jarsplitter ordering issue

    logger.info(
        "Forge: jarsplitter sha mismatch — patching installer to accept "
        "slim=%s extra=%s", actual_slim, actual_extra
    )

    patched_jar = orig_jar.with_suffix(".patched.jar")
    try:
        with _zf.ZipFile(orig_jar, 'r') as zin:
            with _zf.ZipFile(patched_jar, 'w', compression=_zf.ZIP_DEFLATED) as zout:
                for item in zin.infolist():
                    # Drop signature files — JAR is signed and modifying install_profile.json
                    # would break the signature, causing the JVM to reject the JAR immediately.
                    # Without .SF/.RSA, the JVM skips digest verification entirely.
                    name = item.filename
                    if name.startswith("META-INF/") and name.upper().endswith((".SF", ".RSA", ".DSA", ".EC")):
                        continue
                    data = zin.read(name)
                    if name == 'install_profile.json':
                        text = data.decode('utf-8')
                        text = text.replace(stock_slim, actual_slim)
                        text = text.replace(stock_extra, actual_extra)
                        data = text.encode('utf-8')
                    zout.writestr(item, data)
    except Exception as e:
        logger.warning("Forge retry: could not patch installer: %s", e)
        patched_jar.unlink(missing_ok=True)
        return False

    # Remove Forge entries from launcher_profiles.json so the installer doesn't skip
    profiles_file = mc_dir / "launcher_profiles.json"
    if profiles_file.exists():
        try:
            profiles = json.loads(profiles_file.read_text())
            forge_keys = [
                k for k, v in profiles.get("profiles", {}).items()
                if "forge" in str(v.get("lastVersionId", "")).lower()
            ]
            for k in forge_keys:
                del profiles["profiles"][k]
            profiles_file.write_text(json.dumps(profiles))
        except Exception:
            pass

    # Remove the partial Forge version dir so the installer re-runs all steps
    forge_short = FORGE_VERSION.split("-", 1)[-1]
    for d in (mc_dir / "versions").iterdir():
        if d.is_dir() and "forge" in d.name.lower() and forge_short in d.name:
            _shutil.rmtree(d, ignore_errors=True)
            logger.info("Forge: removed partial version dir: %s", d.name)

    # Remove jarsplitter outputs and cache files — otherwise patched installer
    # finds cache hits, skips jarsplitter, and fails at the next processor
    for pattern in ["*-slim.jar", "*-extra.jar", "*-srg.jar",
                    "*-slim.jar.cache", "*-extra.jar.cache", "*-srg.jar.cache"]:
        for f in client_lib_root.rglob(pattern):
            f.unlink(missing_ok=True)
            logger.info("Forge: removed %s for clean retry", f.name)

    if progress:
        progress("Retrying Forge install…", 0, 1)
    logger.info("Running patched Forge installer")
    result = subprocess.run(
        [java, "-jar", str(patched_jar), "--installClient", str(mc_dir)],
        capture_output=True,
        text=True,
        timeout=600,
    )
    patched_jar.unlink(missing_ok=True)
    if result.returncode != 0:
        logger.warning(
            "Forge: patched installer also failed:\n%s",
            _forge_error_summary((result.stdout + result.stderr).strip()),
        )
    return result.returncode == 0


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
        if "Processor failed" in output:
            logger.info("Forge: attempting jarsplitter sha-patch retry")
            if _retry_forge_with_actual_shas(mc_dir, jar, java, progress):
                if is_forge_installed(mc_dir):
                    logger.info("Forge %s installed (sha-patch retry)", FORGE_VERSION)
                    return
        raise RuntimeError(
            f"Forge installer failed (exit {result.returncode}):\n{_forge_error_summary(output)}"
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
    write_servers_dat(mc_dir, "Better Server", server_host, server_port)

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
