"""
Mod sync: SHA256-verified streaming downloads from the pack server.
Manifest-based: download new/changed, remove stale, prune empty dirs.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import requests

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str, int, int], None]


@dataclass
class SyncStats:
    downloaded: int = 0
    removed: int = 0
    unchanged: int = 0
    bytes_downloaded: int = 0


def fetch_server_info(gist_url: str, timeout: float = 10.0) -> dict:
    """Fetch the Gist JSON (server info + launcher version + Tailscale key)."""
    logger.info("Fetching server info from %s", gist_url)
    resp = requests.get(gist_url, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def fetch_manifest(server_url: str, timeout: float = 15.0) -> dict:
    url = server_url.rstrip("/") + "/manifest"
    logger.info("Fetching manifest from %s", url)
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    if "groups" not in data:
        raise ValueError("Response is not a valid pack manifest")
    return data


def get_changelog(server_info: dict, last_manifest: dict) -> str | None:
    """
    Return changelog text if the pack version changed since last launch.
    server_info fields: pack_version (str), pack_changelog (str).
    last_manifest field: version (str).
    """
    current = server_info.get("pack_version", "")
    previous = last_manifest.get("version", "")
    changelog = server_info.get("pack_changelog", "")
    if current and previous and current != previous and changelog:
        return changelog
    return None


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _local_state(instance_dir: Path, manifest: dict) -> dict[str, str]:
    """Build {rel_path: sha256} for all files in sync groups."""
    state: dict[str, str] = {}
    for group in manifest["groups"]:
        group_root = instance_dir / group
        if not group_root.is_dir():
            continue
        for path in group_root.rglob("*"):
            if not path.is_file() or path.name.startswith("."):
                continue
            rel = str(path.relative_to(instance_dir)).replace("\\", "/")
            state[rel] = _sha256(path)
    return state


def sync(
    server_url: str,
    manifest: dict,
    instance_dir: Path,
    progress: ProgressCallback | None = None,
) -> SyncStats:
    stats = SyncStats()
    local = _local_state(instance_dir, manifest)

    server_files: dict[str, dict] = {}
    for group, files in manifest["groups"].items():
        for entry in files:
            server_files[entry["path"]] = entry

    to_download = [e for path, e in server_files.items() if local.get(path) != e["sha256"]]
    sync_groups = set(manifest["groups"])
    to_remove = [p for p in local if p.split("/", 1)[0] in sync_groups and p not in server_files]

    for i, rel_path in enumerate(to_remove):
        if progress:
            progress(f"removing {rel_path}", i + 1, len(to_remove))
        try:
            (instance_dir / rel_path).unlink()
            stats.removed += 1
        except OSError as e:
            logger.warning("Could not remove %s: %s", rel_path, e)

    _prune_empty_dirs(instance_dir, sync_groups)

    session = requests.Session()
    for i, entry in enumerate(to_download):
        rel_path = entry["path"]
        target = instance_dir / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        url = server_url.rstrip("/") + "/files/" + rel_path

        if progress:
            progress(f"downloading {rel_path}", i + 1, len(to_download))

        tmp = target.with_suffix(target.suffix + ".part")
        h = hashlib.sha256()
        with session.get(url, stream=True, timeout=120.0) as resp:
            resp.raise_for_status()
            with tmp.open("wb") as fh:
                for chunk in resp.iter_content(64 * 1024):
                    fh.write(chunk)
                    h.update(chunk)

        got = h.hexdigest()
        if got != entry["sha256"]:
            tmp.unlink(missing_ok=True)
            raise ValueError(f"Checksum mismatch: {rel_path} (got {got[:8]}…, want {entry['sha256'][:8]}…)")
        tmp.replace(target)
        stats.downloaded += 1
        stats.bytes_downloaded += entry.get("size", 0)

    stats.unchanged = len(server_files) - len(to_download)
    return stats


def _prune_empty_dirs(instance_dir: Path, sync_groups: set[str]) -> None:
    for group in sync_groups:
        group_root = instance_dir / group
        if not group_root.is_dir():
            continue
        for path in sorted(group_root.rglob("*"), reverse=True):
            if path.is_dir():
                try:
                    path.rmdir()
                except OSError:
                    pass


def save_last_manifest(path: Path, manifest: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2))


def load_last_manifest(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
