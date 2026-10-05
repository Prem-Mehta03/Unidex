import sqlite3
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from tests.unit._catalog_data import ALIASES, chat_views
from unidex.api.app import create_app
from unidex.auth.google import GoogleIdentity, GoogleOAuth
from unidex.config import Settings
from unidex.db.connection import connect, init_schema
from unidex.exceptions import AuthError
from unidex.search.catalog import Catalog

DOMAIN = "goa.bits-pilani.ac.in"
SECRET = "s" * 40


class FakeGoogle(GoogleOAuth):
    """Pretends to be Google: the 'code' you send decides who signs in."""

    def __init__(self) -> None:
        super().__init__("client", "secret")
        self.people: dict[str, GoogleIdentity] = {
            "college": GoogleIdentity(f"f2024@{DOMAIN}", "College Student", DOMAIN),
            "gmail": GoogleIdentity("someone@gmail.com", "Outsider", None),
            "lookalike": GoogleIdentity(f"f2024@{DOMAIN}", "Imposter", None),
        }

    def finish(self, code: str, redirect_uri: str, verifier: str, nonce: str) -> GoogleIdentity:
        if code not in self.people:
            raise AuthError("bad code")
        return self.people[code]


def settings_for(db_path: Path, allowed_emails: tuple[str, ...] = ()) -> Settings:
    return Settings(
        db_path=db_path,
        log_level="INFO",
        google_client_id="client",
        google_client_secret="secret",
        session_secret=SECRET,
        allowed_email_domains=(DOMAIN,),
        allowed_emails=allowed_emails,
        public_url="https://unidex.test",
    )


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "t.db"
    conn = connect(path)
    init_schema(conn)
    conn.close()
    return path


@pytest.fixture
def client(db_path: Path) -> Iterator[TestClient]:
    catalog = Catalog(chat_views(), course_aliases=ALIASES)
    app = create_app(
        catalog=catalog, settings=settings_for(db_path), oauth=FakeGoogle(), web_dir=None
    )
    with TestClient(app, base_url="https://unidex.test", follow_redirects=False) as c:
        yield c


def sign_in(client: TestClient, who: str = "college") -> str:
    """Run the full redirect dance; return where the browser ends up."""
    start = client.get("/auth/login")
    assert start.status_code == 303
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    done = client.get("/auth/callback", params={"code": who, "state": state})
    assert done.status_code == 303
    return done.headers["location"]


PROTECTED = [
    ("get", "/api/search?q=oop"),
    ("get", "/api/suggest?prefix=oo"),
    ("get", "/api/facets"),
    ("get", "/api/chat/options"),
    ("post", "/api/chat/message"),
    ("post", "/api/chat/results"),
    ("post", "/api/report"),
]


@pytest.mark.parametrize(("method", "path"), PROTECTED)
def test_data_routes_need_a_login(client: TestClient, method: str, path: str) -> None:
    response = getattr(client, method)(path)
    assert response.status_code == 401
    assert response.json()["detail"] == "login_required"


def test_health_and_me_are_public(client: TestClient) -> None:
    assert client.get("/api/health").status_code == 200
    body = client.get("/api/me").json()
    assert body["login_required"] is True
    assert body["signed_in"] is False
    assert body["allowed_domains"] == [DOMAIN]


def test_login_redirects_to_google_with_a_domain_hint(client: TestClient) -> None:
    response = client.get("/auth/login")
    target = urlparse(response.headers["location"])
    assert target.netloc == "accounts.google.com"
    assert parse_qs(target.query)["hd"] == [DOMAIN]
    assert parse_qs(target.query)["redirect_uri"] == ["https://unidex.test/auth/callback"]


def test_college_account_gets_in(client: TestClient) -> None:
    assert sign_in(client) == "/"
    me = client.get("/api/me").json()
    assert me["signed_in"] is True
    assert me["email"] == f"f2024@{DOMAIN}"
    assert me["name"] == "College Student"
    assert client.get("/api/search?q=oop").status_code == 200
    assert client.get("/api/chat/options").status_code == 200


@pytest.mark.parametrize("who", ["gmail", "lookalike"])
def test_other_accounts_are_refused_and_told_why(client: TestClient, who: str) -> None:
    sign_in(client, who)
    me = client.get("/api/me").json()
    assert me["signed_in"] is False
    assert me["error"] == "domain"
    assert client.get("/api/me").json()["error"] == ""  # reported once
    assert client.get("/api/search?q=oop").status_code == 401


def test_wrong_state_is_refused(client: TestClient) -> None:
    client.get("/auth/login")
    response = client.get("/auth/callback", params={"code": "college", "state": "forged"})
    assert response.status_code == 303
    assert client.get("/api/me").json()["error"] == "failed"
    assert client.get("/api/search?q=oop").status_code == 401


def test_callback_without_starting_is_refused(client: TestClient) -> None:
    client.get("/auth/callback", params={"code": "college", "state": "x"})
    assert client.get("/api/me").json()["signed_in"] is False


def test_google_refusing_is_reported(client: TestClient) -> None:
    start = client.get("/auth/login")
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    client.get("/auth/callback", params={"code": "nope", "state": state})
    assert client.get("/api/me").json()["error"] == "failed"


def test_cancelled_sign_in(client: TestClient) -> None:
    client.get("/auth/login")
    client.get("/auth/callback", params={"error": "access_denied"})
    assert client.get("/api/me").json()["error"] == "cancelled"


def test_a_state_can_be_used_only_once(client: TestClient) -> None:
    start = client.get("/auth/login")
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    client.get("/auth/callback", params={"code": "college", "state": state})
    client.post("/auth/logout")
    client.get("/auth/callback", params={"code": "college", "state": state})
    assert client.get("/api/me").json()["signed_in"] is False


def test_logout_ends_the_session(client: TestClient) -> None:
    sign_in(client)
    assert client.post("/auth/logout").status_code == 204
    assert client.get("/api/search?q=oop").status_code == 401


def test_session_cookie_is_locked_down(client: TestClient) -> None:
    client.get("/auth/login")
    cookie = client.get("/auth/login").headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    assert "secure" in cookie


def test_tampered_cookie_is_ignored(client: TestClient) -> None:
    sign_in(client)
    client.cookies.set("unidex_session", "forged.value.here")
    assert client.get("/api/me").json()["signed_in"] is False


def test_searches_are_logged_under_a_hash_not_an_email(client: TestClient, db_path: Path) -> None:
    sign_in(client)
    client.get("/api/search?q=oop+midsem")
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT user_hash, query, result_count, filters FROM search_logs"
    ).fetchall()
    conn.close()
    assert len(rows) == 1
    user_hash, query, count, filters = rows[0]
    assert query == "oop midsem"
    assert count > 0
    assert len(user_hash) == 16
    assert "bits" not in user_hash + filters
    assert '"source": "search"' in filters


def test_site_is_open_when_login_is_not_configured() -> None:
    catalog = Catalog(chat_views(), course_aliases=ALIASES)
    with TestClient(create_app(catalog=catalog, web_dir=None)) as open_client:
        assert open_client.get("/api/search?q=oop").status_code == 200
        me = open_client.get("/api/me").json()
        assert me["login_required"] is False
        assert me["signed_in"] is False
        assert open_client.get("/auth/login", follow_redirects=False).status_code == 303
        assert open_client.post("/auth/logout").status_code == 204


def test_switch_drops_the_domain_hint(client: TestClient) -> None:
    normal = parse_qs(urlparse(client.get("/auth/login").headers["location"]).query)
    switched = parse_qs(urlparse(client.get("/auth/login?switch=1").headers["location"]).query)
    assert "hd" in normal
    assert "hd" not in switched
    assert switched["prompt"] == ["select_account"]


def test_an_individually_allowed_gmail_can_sign_in(db_path: Path) -> None:
    catalog = Catalog(chat_views(), course_aliases=ALIASES)
    app = create_app(
        catalog=catalog,
        settings=settings_for(db_path, ("someone@gmail.com",)),
        oauth=FakeGoogle(),
        web_dir=None,
    )
    with TestClient(app, base_url="https://unidex.test", follow_redirects=False) as c:
        sign_in(c, "gmail")
        assert c.get("/api/me").json()["signed_in"] is True


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_api_pages_are_hidden_when_login_is_on(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 404
