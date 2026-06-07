"""
Microsoft OAuth (PKCE) + offline account support.
No UI here — account selection lives in ui/prelaunch.py.
"""
from __future__ import annotations

import json
import logging
import threading
import uuid
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from minecraft_launcher_lib import microsoft_account

from .config import OAUTH_CLIENT_ID, OAUTH_PORT, OAUTH_REDIRECT

logger = logging.getLogger(__name__)


@dataclass
class MinecraftAccount:
    username: str
    uuid: str
    access_token: str
    refresh_token: str | None
    offline: bool = False


# ── OAuth callback server ──────────────────────────────────────────────────────

import queue as _queue_mod

def _make_handler(result_queue: "_queue_mod.Queue[tuple[str, str]]"):
    class _CallbackHandler(BaseHTTPRequestHandler):
        def log_message(self, *args, **kwargs) -> None:
            pass

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            # Ignore favicon and other noise — only accept the root callback
            if parsed.path != "/" or "code" not in parsed.query:
                self.send_response(204)
                self.end_headers()
                return
            params = parse_qs(parsed.query)
            code  = params.get("code",  [None])[0]
            state = params.get("state", [None])[0]
            if code:
                result_queue.put((code, state or ""))
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                b"<html><head><title>Logged in</title></head>"
                b"<body style='font-family:system-ui;max-width:30em;margin:4em auto;text-align:center'>"
                b"<h2>You can close this tab.</h2>"
                b"<p>Homestead received your login &mdash; return to the launcher.</p>"
                b"</body></html>"
            )
    return _CallbackHandler


# ── Auth flows ─────────────────────────────────────────────────────────────────

def login_microsoft() -> MinecraftAccount:
    """Full interactive Microsoft login (opens browser, waits for redirect)."""
    login_url, state, verifier = microsoft_account.get_secure_login_data(
        client_id=OAUTH_CLIENT_ID,
        redirect_uri=OAUTH_REDIRECT,
    )
    result_queue: _queue_mod.Queue[tuple[str, str]] = _queue_mod.Queue()
    server = HTTPServer(("127.0.0.1", OAUTH_PORT), _make_handler(result_queue))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        webbrowser.open(login_url)
        try:
            code, received_state = result_queue.get(timeout=300)
        except _queue_mod.Empty:
            raise TimeoutError("Timed out waiting for Microsoft login")
        if received_state != state:
            raise ValueError("OAuth state mismatch — possible CSRF")
        result = microsoft_account.complete_login(
            client_id=OAUTH_CLIENT_ID,
            client_secret=None,
            redirect_uri=OAUTH_REDIRECT,
            auth_code=code,
            code_verifier=verifier,
        )
    finally:
        server.shutdown()
    return MinecraftAccount(
        username=result["name"],
        uuid=result["id"],
        access_token=result["access_token"],
        refresh_token=result["refresh_token"],
        offline=False,
    )


def login_offline(username: str) -> MinecraftAccount:
    """Create an offline account with a deterministic UUID (Notchian convention)."""
    return MinecraftAccount(
        username=username,
        uuid=str(uuid.uuid3(uuid.NAMESPACE_DNS, f"OfflinePlayer:{username}")),
        access_token="0",
        refresh_token=None,
        offline=True,
    )


def refresh_account(refresh_token: str) -> MinecraftAccount:
    """Silently get a fresh access token from a stored refresh token."""
    result = microsoft_account.complete_refresh(
        client_id=OAUTH_CLIENT_ID,
        client_secret=None,
        redirect_uri=None,
        refresh_token=refresh_token,
    )
    return MinecraftAccount(
        username=result["name"],
        uuid=result["id"],
        access_token=result["access_token"],
        refresh_token=result["refresh_token"],
        offline=False,
    )


# ── Persistence ────────────────────────────────────────────────────────────────

def save_account(path: Path, account: MinecraftAccount) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "username": account.username,
        "uuid": account.uuid,
        "refresh_token": account.refresh_token,
        "offline": account.offline,
    }, indent=2))
    try:
        path.chmod(0o600)
    except OSError:
        pass


def load_account(path: Path) -> MinecraftAccount | None:
    """Load saved account, refreshing token if present. Returns None if absent or expired."""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None

    if data.get("offline"):
        return MinecraftAccount(
            username=data["username"],
            uuid=data["uuid"],
            access_token="0",
            refresh_token=None,
            offline=True,
        )

    if data.get("refresh_token"):
        try:
            account = refresh_account(data["refresh_token"])
            save_account(path, account)
            logger.info("Refreshed token for %s", account.username)
            return account
        except Exception as e:
            logger.warning("Token refresh failed: %s — will re-prompt", e)

    return None
