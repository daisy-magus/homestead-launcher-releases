"""
Microsoft OAuth (PKCE) + offline account support.
No UI here — account selection lives in ui/prelaunch.py.
"""
from __future__ import annotations

import json
import logging
import threading
import time
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

class _CallbackHandler(BaseHTTPRequestHandler):
    captured_code: str | None = None
    captured_state: str | None = None

    def log_message(self, *args, **kwargs) -> None:
        pass

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/":
            self.send_response(404)
            self.end_headers()
            return
        params = parse_qs(parsed.query)
        _CallbackHandler.captured_code  = params.get("code",  [None])[0]
        _CallbackHandler.captured_state = params.get("state", [None])[0]
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


def _start_callback_server() -> HTTPServer:
    server = HTTPServer(("127.0.0.1", OAUTH_PORT), _CallbackHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _wait_for_code(timeout: float = 300.0) -> tuple[str, str]:
    deadline = time.time() + timeout
    while _CallbackHandler.captured_code is None:
        if time.time() > deadline:
            raise TimeoutError("Timed out waiting for Microsoft login")
        time.sleep(0.2)
    code  = _CallbackHandler.captured_code
    state = _CallbackHandler.captured_state
    _CallbackHandler.captured_code  = None
    _CallbackHandler.captured_state = None
    return code, state


# ── Auth flows ─────────────────────────────────────────────────────────────────

def login_microsoft() -> MinecraftAccount:
    """Full interactive Microsoft login (opens browser, waits for redirect)."""
    login_url, state, verifier = microsoft_account.get_secure_login_data(
        client_id=OAUTH_CLIENT_ID,
        redirect_uri=OAUTH_REDIRECT,
    )
    server = _start_callback_server()
    try:
        webbrowser.open(login_url)
        code, received_state = _wait_for_code()
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
