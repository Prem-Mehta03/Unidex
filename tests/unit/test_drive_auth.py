import json
import stat
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx2 as httpx
import pytest

from unidex.auth.drive import (
    DRIVE_SCOPE,
    DriveCredentials,
    load_refresh_token,
    run_loopback_login,
    save_refresh_token,
)
from unidex.exceptions import AuthError


def credentials(handler, clock=lambda: 1000.0) -> DriveCredentials:
    return DriveCredentials(
        "cid",
        "csecret",
        "refresh-1",
        http=httpx.Client(transport=httpx.MockTransport(handler)),
        clock=clock,
    )


class TestCredentials:
    def test_refreshes_then_reuses_the_token(self) -> None:
        calls: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(parse_qs(request.content.decode()))
            return httpx.Response(200, json={"access_token": "A1", "expires_in": 3600})

        creds = credentials(handler)
        assert creds.access_token() == "A1"
        assert creds.access_token() == "A1"
        assert len(calls) == 1
        assert calls[0]["grant_type"] == ["refresh_token"]
        assert calls[0]["refresh_token"] == ["refresh-1"]
        assert calls[0]["client_secret"] == ["csecret"]

    def test_asks_again_when_the_token_is_about_to_expire(self) -> None:
        now = [1000.0]
        tokens = iter(["A1", "A2"])

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"access_token": next(tokens), "expires_in": 3600})

        creds = credentials(handler, clock=lambda: now[0])
        assert creds.access_token() == "A1"
        now[0] += 3600 - 30  # inside the safety margin
        assert creds.access_token() == "A2"

    def test_expired_refresh_token_explains_what_to_do(self) -> None:
        creds = credentials(lambda r: httpx.Response(400, json={"error": "invalid_grant"}))
        with pytest.raises(AuthError, match="7 days"):
            creds.access_token()

    def test_network_failure(self) -> None:
        def boom(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down")

        with pytest.raises(AuthError, match="reach"):
            credentials(boom).access_token()

    def test_answer_without_a_token(self) -> None:
        with pytest.raises(AuthError, match="access token"):
            credentials(lambda r: httpx.Response(200, json={})).access_token()


class TestTokenFile:
    def test_round_trip(self, tmp_path: Path) -> None:
        path = tmp_path / "sub" / "token.json"
        save_refresh_token(path, "r-123")
        assert load_refresh_token(path) == "r-123"
        assert json.loads(path.read_text()) == {"refresh_token": "r-123"}

    @pytest.mark.skipif(sys.platform == "win32", reason="no Unix permissions on Windows")
    def test_file_is_private(self, tmp_path: Path) -> None:
        path = tmp_path / "token.json"
        save_refresh_token(path, "r")
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    def test_missing_or_broken_file(self, tmp_path: Path) -> None:
        with pytest.raises(AuthError, match="drive_login"):
            load_refresh_token(tmp_path / "nope.json")
        broken = tmp_path / "bad.json"
        broken.write_text("not json")
        with pytest.raises(AuthError):
            load_refresh_token(broken)


class TestLoopbackLogin:
    def make_browser(self, query_for):
        """A fake browser: 'visits' the Google address, then Google 'redirects' back."""

        def open_browser(url: str) -> None:
            google = parse_qs(urlparse(url).query)
            redirect = google["redirect_uri"][0]
            extra = query_for(google)
            httpx.get(f"{redirect}?{extra}")

        return open_browser

    def test_successful_login_returns_the_refresh_token(self) -> None:
        seen: dict = {}

        def token_endpoint(request: httpx.Request) -> httpx.Response:
            seen.update(parse_qs(request.content.decode()))
            return httpx.Response(200, json={"refresh_token": "R-OK", "access_token": "a"})

        captured: dict = {}

        def query_for(google: dict) -> str:
            captured.update(google)
            return f"code=abc&state={google['state'][0]}"

        token = run_loopback_login(
            "cid",
            "secret",
            http=httpx.Client(transport=httpx.MockTransport(token_endpoint)),
            open_browser=self.make_browser(query_for),
            timeout=5,
        )
        assert token == "R-OK"
        assert captured["scope"] == [DRIVE_SCOPE]
        assert captured["access_type"] == ["offline"]
        assert captured["code_challenge_method"] == ["S256"]
        assert seen["code"] == ["abc"]
        assert seen["code_verifier"]
        assert seen["redirect_uri"] == captured["redirect_uri"]

    def test_wrong_state_is_refused(self) -> None:
        with pytest.raises(AuthError, match="state"):
            run_loopback_login(
                "cid",
                "secret",
                http=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))),
                open_browser=self.make_browser(lambda g: "code=abc&state=forged"),
                timeout=5,
            )

    def test_person_says_no(self) -> None:
        with pytest.raises(AuthError, match="access_denied"):
            run_loopback_login(
                "cid",
                "secret",
                http=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))),
                open_browser=self.make_browser(lambda g: "error=access_denied"),
                timeout=5,
            )

    def test_nobody_comes_back(self) -> None:
        with pytest.raises(AuthError, match="Timed out"):
            run_loopback_login(
                "cid",
                "secret",
                http=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))),
                open_browser=lambda url: None,
                timeout=0.5,
            )

    def test_google_sends_no_refresh_token(self) -> None:
        with pytest.raises(AuthError, match="no refresh token"):
            run_loopback_login(
                "cid",
                "secret",
                http=httpx.Client(
                    transport=httpx.MockTransport(
                        lambda r: httpx.Response(200, json={"access_token": "a"})
                    )
                ),
                open_browser=self.make_browser(lambda g: f"code=c&state={g['state'][0]}"),
                timeout=5,
            )
