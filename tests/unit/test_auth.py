import base64
import json
from urllib.parse import parse_qs, urlparse

import httpx2 as httpx
import pytest

from unidex.auth.google import GoogleOAuth, decode_claims
from unidex.auth.policy import email_domain, is_allowed, user_hash
from unidex.exceptions import AuthError

DOMAIN = "goa.bits-pilani.ac.in"
CLIENT_ID = "client-123"
NOW = 1_800_000_000.0


class TestPolicy:
    def test_college_account_is_allowed(self) -> None:
        assert is_allowed(f"f2024@{DOMAIN}", DOMAIN, [DOMAIN])

    def test_case_does_not_matter(self) -> None:
        assert is_allowed(f"F2024@{DOMAIN.upper()}", DOMAIN.upper(), [DOMAIN])

    @pytest.mark.parametrize(
        "email",
        [
            "someone@gmail.com",
            f"x@evil-{DOMAIN}",
            f"x@{DOMAIN}.evil.com",
            f"x@sub.{DOMAIN}",
            "no-at-sign",
            "",
        ],
    )
    def test_lookalike_domains_are_refused(self, email: str) -> None:
        assert not is_allowed(email, DOMAIN, [DOMAIN])

    def test_personal_account_using_a_college_address_has_no_hosted_domain(self) -> None:
        assert not is_allowed(f"x@{DOMAIN}", None, [DOMAIN])

    def test_hosted_domain_must_match_the_email(self) -> None:
        assert not is_allowed(f"x@{DOMAIN}", "other.edu", [DOMAIN])

    def test_several_domains(self) -> None:
        assert is_allowed("x@a.edu", "a.edu", ["a.edu", "b.edu"])

    def test_individually_allowed_address_gets_in_without_a_college_domain(self) -> None:
        assert is_allowed("Me@gmail.com", None, [DOMAIN], ["me@gmail.com"])
        assert not is_allowed("other@gmail.com", None, [DOMAIN], ["me@gmail.com"])
        assert not is_allowed("me@gmail.com.evil.com", None, [DOMAIN], ["me@gmail.com"])

    def test_email_domain(self) -> None:
        assert email_domain(" A@B.com ") == "b.com"
        assert email_domain("nope") == ""

    def test_user_hash_is_stable_short_and_keyed(self) -> None:
        first = user_hash("A@x.com", "secret-one")
        assert first == user_hash(" a@x.com", "secret-one")
        assert first != user_hash("a@x.com", "secret-two")
        assert len(first) == 16
        assert "x.com" not in first


def make_token(**overrides: object) -> str:
    claims: dict[str, object] = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "exp": NOW + 600,
        "nonce": "n-1",
        "email": f"Student@{DOMAIN}",
        "email_verified": True,
        "name": "A Student",
        "hd": DOMAIN,
    }
    claims.update(overrides)
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return f"header.{body}.signature"


def oauth_with(handler: httpx.MockTransport | None = None, **kw: object) -> GoogleOAuth:
    transport = handler or httpx.MockTransport(
        lambda request: httpx.Response(200, json={"id_token": make_token(**kw)})
    )
    return GoogleOAuth(
        CLIENT_ID, "secret", http=httpx.Client(transport=transport), clock=lambda: NOW
    )


class TestStart:
    def test_url_has_state_nonce_and_pkce(self) -> None:
        start = oauth_with().start("https://site/auth/callback", domain_hint=DOMAIN)
        query = parse_qs(urlparse(start.url).query)
        assert urlparse(start.url).netloc == "accounts.google.com"
        assert query["client_id"] == [CLIENT_ID]
        assert query["state"] == [start.state]
        assert query["nonce"] == [start.nonce]
        assert query["code_challenge_method"] == ["S256"]
        assert query["hd"] == [DOMAIN]
        assert query["redirect_uri"] == ["https://site/auth/callback"]
        assert start.verifier not in start.url

    def test_each_start_is_different(self) -> None:
        oauth = oauth_with()
        assert oauth.start("u").state != oauth.start("u").state


class TestFinish:
    def finish(self, oauth: GoogleOAuth):
        return oauth.finish("code", "https://site/auth/callback", "verifier", "n-1")

    def test_good_token_gives_an_identity(self) -> None:
        identity = self.finish(oauth_with())
        assert identity.email == f"student@{DOMAIN}"
        assert identity.name == "A Student"
        assert identity.hosted_domain == DOMAIN

    def test_the_request_to_google_carries_the_verifier_and_secret(self) -> None:
        seen: dict[str, list[str]] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(parse_qs(request.content.decode()))
            return httpx.Response(200, json={"id_token": make_token()})

        self.finish(oauth_with(httpx.MockTransport(handler)))
        assert seen["code_verifier"] == ["verifier"]
        assert seen["client_secret"] == ["secret"]
        assert seen["grant_type"] == ["authorization_code"]

    @pytest.mark.parametrize(
        "override",
        [
            {"iss": "https://evil.example"},
            {"aud": "someone-else"},
            {"exp": NOW - 3600},
            {"nonce": "other"},
            {"email_verified": False},
            {"email": ""},
        ],
    )
    def test_bad_claims_are_refused(self, override: dict[str, object]) -> None:
        with pytest.raises(AuthError):
            self.finish(oauth_with(**override))

    def test_audience_list_is_accepted(self) -> None:
        assert self.finish(oauth_with(aud=["x", CLIENT_ID])).email

    def test_google_refusing_the_code(self) -> None:
        transport = httpx.MockTransport(lambda r: httpx.Response(400, json={"error": "bad"}))
        with pytest.raises(AuthError):
            self.finish(oauth_with(transport))

    def test_missing_id_token(self) -> None:
        transport = httpx.MockTransport(lambda r: httpx.Response(200, json={}))
        with pytest.raises(AuthError):
            self.finish(oauth_with(transport))

    def test_network_failure(self) -> None:
        def boom(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down")

        with pytest.raises(AuthError):
            self.finish(oauth_with(httpx.MockTransport(boom)))


@pytest.mark.parametrize("token", ["", "a.b", "a.b.c.d", "a.!!!.c"])
def test_malformed_tokens(token: str) -> None:
    with pytest.raises(AuthError):
        decode_claims(token)


def test_empty_claims_object_decodes() -> None:
    assert decode_claims("a.e30.c") == {}


def test_token_that_is_not_an_object() -> None:
    body = base64.urlsafe_b64encode(b"[1, 2]").rstrip(b"=").decode()
    with pytest.raises(AuthError):
        decode_claims(f"a.{body}.c")
