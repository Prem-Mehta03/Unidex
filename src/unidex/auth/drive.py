"""Read-only access to Google Drive for the sync job.

Two pieces:

* :func:`run_loopback_login` - a one-time sign-in you run on your own computer
  (``scripts/drive_login.py``). Google sends the browser back to a tiny web
  server on ``127.0.0.1``; we swap the code for a long-lived *refresh token* and
  save it in a git-ignored file.
* :class:`DriveCredentials` - turns that refresh token into short-lived access
  tokens whenever the sync needs one.

The scope is ``drive.readonly``: Unidex can list and read files, never change them.
"""

import json
import logging
import os
import secrets
import threading
import time
import webbrowser
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx2 as httpx

from unidex.auth.google import AUTH_URL, TOKEN_URL, pkce_challenge
from unidex.exceptions import AuthError

logger = logging.getLogger(__name__)

DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
LOOPBACK_HOST = "127.0.0.1"
LOGIN_TIMEOUT_SECONDS = 300.0
EXPIRY_MARGIN_SECONDS = 60.0
REQUEST_TIMEOUT_SECONDS = 15.0

_DONE_PAGE = (
    b"<!doctype html><meta charset=utf-8><title>Unidex</title>"
    b"<p style='font:16px system-ui;margin:3rem'>Done. You can close this tab and "
    b"go back to the terminal.</p>"
)


def save_refresh_token(path: Path, refresh_token: str) -> None:
    """Store the refresh token in a file only the current user can read.

    Args:
        path: Where to write the JSON file.
        refresh_token: The long-lived token from Google.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"refresh_token": refresh_token}), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:  # Windows has no Unix permissions; the folder is git-ignored anyway.
        logger.debug("Could not restrict permissions on %s", path)


def load_refresh_token(path: Path) -> str:
    """Read the saved refresh token.

    Args:
        path: The JSON file written by :func:`save_refresh_token`.

    Returns:
        The refresh token.

    Raises:
        AuthError: If the file is missing or unreadable.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return str(data["refresh_token"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise AuthError(
            f"No Drive sign-in found at {path}. Run: python scripts/drive_login.py"
        ) from exc


class DriveCredentials:
    """Hands out access tokens, refreshing them when they run out."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        *,
        http: httpx.Client | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """Create the credentials.

        Args:
            client_id: OAuth client id.
            client_secret: OAuth client secret.
            refresh_token: The saved long-lived token.
            http: HTTP client; tests pass one with a fake transport.
            clock: Returns seconds; tests pass a fixed one.
        """
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token
        self._http = http or httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS)
        self._clock = clock
        self._token = ""
        self._expires_at = 0.0

    def access_token(self) -> str:
        """Return a valid access token, asking Google for a new one if needed.

        Returns:
            The bearer token.

        Raises:
            AuthError: If Google refuses the refresh token (for example because it expired).
        """
        if self._token and self._clock() < self._expires_at - EXPIRY_MARGIN_SECONDS:
            return self._token
        try:
            response = self._http.post(
                TOKEN_URL,
                data={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "refresh_token": self._refresh_token,
                    "grant_type": "refresh_token",
                },
            )
        except httpx.HTTPError as exc:
            raise AuthError("Could not reach Google to refresh the Drive sign-in") from exc
        if response.status_code != httpx.codes.OK:
            raise AuthError(
                "Google refused the saved Drive sign-in (HTTP "
                f"{response.status_code}). If the OAuth app is in Testing mode the sign-in "
                "expires after 7 days: run python scripts/drive_login.py again."
            )
        try:
            body = response.json()
            self._token = str(body["access_token"])
            self._expires_at = self._clock() + float(body.get("expires_in", 3600))
        except (ValueError, KeyError, TypeError) as exc:
            raise AuthError("Google's answer had no access token") from exc
        return self._token


class _Receiver(HTTPServer):
    """A one-shot web server that remembers the query string it was called with."""

    result: dict[str, list[str]] | None = None


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        server: _Receiver = self.server  # type: ignore[assignment]
        query = parse_qs(urlparse(self.path).query)
        if "code" in query or "error" in query:
            server.result = query
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(_DONE_PAGE)

    def log_message(self, format: str, *args: object) -> None:
        """Stay quiet: the default prints every request to the terminal."""


def run_loopback_login(
    client_id: str,
    client_secret: str,
    *,
    http: httpx.Client | None = None,
    open_browser: Callable[[str], object] = webbrowser.open,
    timeout: float = LOGIN_TIMEOUT_SECONDS,
) -> str:
    """Sign in once in the browser and return the refresh token.

    Args:
        client_id: OAuth client id (type *Desktop app*).
        client_secret: OAuth client secret.
        http: HTTP client; tests pass one with a fake transport.
        open_browser: Opens the sign-in address; tests replace it.
        timeout: Seconds to wait for the person to finish.

    Returns:
        The refresh token.

    Raises:
        AuthError: On timeout, refusal, a forged callback or a failed exchange.
    """
    client = http or httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS)
    server = _Receiver((LOOPBACK_HOST, 0), _Handler)
    server.timeout = 0.2
    redirect_uri = f"http://{LOOPBACK_HOST}:{server.server_address[1]}/"
    state = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(48)
    url = (
        AUTH_URL
        + "?"
        + urlencode(
            {
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": DRIVE_SCOPE,
                "state": state,
                "code_challenge": pkce_challenge(verifier),
                "code_challenge_method": "S256",
                "access_type": "offline",
                "prompt": "consent",
            }
        )
    )
    logger.info("Opening your browser. If nothing opens, visit this address:\n%s", url)
    worker = threading.Thread(target=lambda: _serve_until(server, timeout), daemon=True)
    worker.start()
    try:
        open_browser(url)
        worker.join(timeout + 1)
    finally:
        server.server_close()
    query = server.result
    if query is None:
        raise AuthError("Timed out waiting for the Google sign-in")
    if "error" in query:
        raise AuthError(f"Google reported: {query['error'][0]}")
    if not secrets.compare_digest(query.get("state", [""])[0], state):
        raise AuthError("The sign-in came back with the wrong state; try again")
    return _exchange(client, client_id, client_secret, query["code"][0], redirect_uri, verifier)


def _serve_until(server: _Receiver, timeout: float) -> None:
    """Handle requests until the callback arrives or time runs out."""
    deadline = time.monotonic() + timeout
    while server.result is None and time.monotonic() < deadline:
        server.handle_request()


def _exchange(
    client: httpx.Client,
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
    verifier: str,
) -> str:
    """Swap the one-time code for a refresh token."""
    try:
        response = client.post(
            TOKEN_URL,
            data={
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
                "code_verifier": verifier,
            },
        )
    except httpx.HTTPError as exc:
        raise AuthError("Could not reach Google to finish the sign-in") from exc
    if response.status_code != httpx.codes.OK:
        raise AuthError(f"Google did not accept the sign-in (HTTP {response.status_code})")
    try:
        return str(response.json()["refresh_token"])
    except (ValueError, KeyError) as exc:
        raise AuthError(
            "Google sent no refresh token. Remove Unidex at "
            "myaccount.google.com/permissions and try again."
        ) from exc
