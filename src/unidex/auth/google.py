"""Google sign-in using the OpenID Connect "authorization code" flow.

The steps, in plain words:

1. We send the browser to Google with a random ``state`` (to stop forged
   callbacks), a ``nonce`` (to stop replayed tokens) and a PKCE challenge.
2. The person signs in. Google sends the browser back with a one-time ``code``.
3. We swap the code for an ID token by calling Google's token address directly
   over HTTPS, proving who we are with the client secret and the PKCE verifier.
4. We read the token's claims. Because the token came straight from Google over
   TLS in step 3, the OpenID Connect specification allows us to trust it without
   re-checking its signature; we still check the issuer, audience, expiry, nonce
   and ``email_verified``.
"""

import base64
import binascii
import hashlib
import json
import logging
import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx2 as httpx

from unidex.exceptions import AuthError

logger = logging.getLogger(__name__)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 (a web address, not a password)
ISSUERS = frozenset({"https://accounts.google.com", "accounts.google.com"})
REQUEST_TIMEOUT_SECONDS = 10.0
CLOCK_SKEW_SECONDS = 60


@dataclass(frozen=True, slots=True)
class GoogleIdentity:
    """Who Google says signed in.

    Attributes:
        email: Verified email address.
        name: Display name (may be empty).
        hosted_domain: The ``hd`` claim (the Workspace domain), or ``None``.
    """

    email: str
    name: str
    hosted_domain: str | None


@dataclass(frozen=True, slots=True)
class LoginStart:
    """What the browser needs to begin a sign-in.

    Attributes:
        url: Google address to send the browser to.
        state: Random value to compare when the browser returns.
        nonce: Random value that must appear inside the ID token.
        verifier: PKCE secret, kept in the session until the code is exchanged.
    """

    url: str
    state: str
    nonce: str
    verifier: str


def _challenge(verifier: str) -> str:
    """Return the PKCE S256 challenge for a verifier."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def decode_claims(id_token: str) -> dict[str, Any]:
    """Read the claims inside an ID token (no signature check; see module note).

    Args:
        id_token: A JSON Web Token: three dot-separated base64url parts.

    Returns:
        The claims.

    Raises:
        AuthError: If the token is not shaped like a JWT.
    """
    parts = id_token.split(".")
    if len(parts) != 3:
        raise AuthError("Google sent a malformed ID token")
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except (binascii.Error, ValueError) as exc:
        raise AuthError("Google sent an unreadable ID token") from exc
    if not isinstance(claims, dict):
        raise AuthError("Google sent an unreadable ID token")
    return claims


class GoogleOAuth:
    """Talks to Google for one sign-in at a time."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        *,
        http: httpx.Client | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """Create the helper.

        Args:
            client_id: OAuth client id from Google Cloud.
            client_secret: OAuth client secret.
            http: HTTP client; tests pass one with a fake transport.
            clock: Returns the time in seconds; tests pass a fixed one.
        """
        self._client_id = client_id
        self._client_secret = client_secret
        self._http = http or httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS)
        self._clock = clock

    def start(self, redirect_uri: str, domain_hint: str | None = None) -> LoginStart:
        """Build the Google address that begins a sign-in.

        Args:
            redirect_uri: Where Google should send the browser back to.
            domain_hint: Preselects a Workspace domain on Google's page. It is only a
                convenience; the real check happens after sign-in.

        Returns:
            The address plus the random values to remember.
        """
        state = secrets.token_urlsafe(24)
        nonce = secrets.token_urlsafe(24)
        verifier = secrets.token_urlsafe(48)
        params = {
            "client_id": self._client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "nonce": nonce,
            "code_challenge": _challenge(verifier),
            "code_challenge_method": "S256",
            "prompt": "select_account",
        }
        if domain_hint:
            params["hd"] = domain_hint
        return LoginStart(f"{AUTH_URL}?{urlencode(params)}", state, nonce, verifier)

    def finish(self, code: str, redirect_uri: str, verifier: str, nonce: str) -> GoogleIdentity:
        """Swap a one-time code for the person's identity.

        Args:
            code: The ``code`` Google put on the callback address.
            redirect_uri: The same address used in :meth:`start`.
            verifier: The PKCE verifier from :meth:`start`.
            nonce: The nonce from :meth:`start`.

        Returns:
            The verified identity.

        Raises:
            AuthError: If Google refuses the code or the token fails a check.
        """
        try:
            response = self._http.post(
                TOKEN_URL,
                data={
                    "code": code,
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                    "code_verifier": verifier,
                },
            )
        except httpx.HTTPError as exc:
            raise AuthError("Could not reach Google to finish signing in") from exc
        if response.status_code != httpx.codes.OK:
            logger.warning("Google rejected the sign-in code (HTTP %s)", response.status_code)
            raise AuthError("Google did not accept the sign-in")
        try:
            id_token = str(response.json()["id_token"])
        except (ValueError, KeyError) as exc:
            raise AuthError("Google's answer had no ID token") from exc
        return self._identity(decode_claims(id_token), nonce)

    def _identity(self, claims: Mapping[str, Any], nonce: str) -> GoogleIdentity:
        """Check the claims and build an identity."""
        if claims.get("iss") not in ISSUERS:
            raise AuthError("The ID token came from an unexpected issuer")
        audience = claims.get("aud")
        if audience != self._client_id and not (
            isinstance(audience, list) and self._client_id in audience
        ):
            raise AuthError("The ID token was issued for a different app")
        expires = claims.get("exp")
        if not isinstance(expires, int | float) or expires + CLOCK_SKEW_SECONDS < self._clock():
            raise AuthError("The ID token has expired")
        if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
            raise AuthError("The ID token does not match this sign-in")
        email = str(claims.get("email", "")).strip().lower()
        if not email or claims.get("email_verified") is not True:
            raise AuthError("Google has not verified this email address")
        hosted = claims.get("hd")
        return GoogleIdentity(
            email=email,
            name=str(claims.get("name", "")).strip(),
            hosted_domain=str(hosted).lower() if hosted else None,
        )
