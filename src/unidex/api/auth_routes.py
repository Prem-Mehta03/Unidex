"""Sign-in routes: ``/auth/login``, ``/auth/callback``, ``/auth/logout`` and ``/api/me``.

The signed-in person is kept in a signed cookie (Starlette's session). When login
is not configured, the whole site is open and ``/api/me`` says so; that is the
normal mode while developing on your own computer.
"""

import logging
import secrets
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from unidex.api.schemas import MeResponse
from unidex.auth.google import GoogleOAuth
from unidex.auth.policy import is_allowed, user_hash
from unidex.config import Settings
from unidex.exceptions import AuthError

logger = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])

SESSION_OAUTH = "oauth"
SESSION_USER = "user"
SESSION_ERROR = "login_error"
ANONYMOUS = "anonymous"

ERROR_DOMAIN = "domain"
ERROR_FAILED = "failed"
ERROR_CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class User:
    """The person making a request.

    Attributes:
        label: Non-reversible label used in logs (never the email).
        email: Email address, empty when login is switched off.
        name: Display name, empty when unknown.
    """

    label: str
    email: str = ""
    name: str = ""


def login_required(request: Request) -> bool:
    """Whether this server asks people to sign in."""
    return bool(request.app.state.oauth is not None)


def current_user(request: Request) -> User | None:
    """Return the signed-in person, or ``None`` when nobody is signed in."""
    if not login_required(request):
        return User(ANONYMOUS)
    stored = request.session.get(SESSION_USER)
    if not isinstance(stored, dict) or not stored.get("email"):
        return None
    settings: Settings = request.app.state.settings
    email = str(stored["email"])
    return User(user_hash(email, settings.session_secret or ""), email, str(stored.get("name", "")))


def require_user(request: Request) -> User:
    """FastAPI dependency: the signed-in person, or a 401 answer."""
    user = current_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="login_required")
    return user


CurrentUser = Annotated[User, Depends(require_user)]


def _redirect_uri(request: Request) -> str:
    settings: Settings = request.app.state.settings
    base = settings.public_url or str(request.base_url).rstrip("/")
    return f"{base}/auth/callback"


def _home(error: str | None, request: Request) -> RedirectResponse:
    if error:
        request.session[SESSION_ERROR] = error
    return RedirectResponse("/", status_code=303)


@router.get("/auth/login")
def login(request: Request, switch: bool = False) -> RedirectResponse:
    """Send the browser to Google's sign-in page.

    Args:
        request: The request.
        switch: True after a refused sign-in. The college-domain hint is left out so Google
            shows every account the browser knows, letting the person pick another one.
    """
    oauth: GoogleOAuth | None = request.app.state.oauth
    if oauth is None:
        return RedirectResponse("/", status_code=303)
    settings: Settings = request.app.state.settings
    single = len(settings.allowed_email_domains) == 1
    hint = settings.allowed_email_domains[0] if single and not switch else None
    start = oauth.start(_redirect_uri(request), domain_hint=hint)
    request.session[SESSION_OAUTH] = {
        "state": start.state,
        "nonce": start.nonce,
        "verifier": start.verifier,
    }
    return RedirectResponse(start.url, status_code=303)


@router.get("/auth/callback")
def callback(
    request: Request, code: str = "", state: str = "", error: str = ""
) -> RedirectResponse:
    """Finish the sign-in Google sent the browser back from."""
    oauth: GoogleOAuth | None = request.app.state.oauth
    if oauth is None:
        return RedirectResponse("/", status_code=303)
    settings: Settings = request.app.state.settings
    pending = request.session.pop(SESSION_OAUTH, None)
    if error:
        logger.info("Sign-in cancelled or refused by Google (%s)", error[:40])
        return _home(ERROR_CANCELLED, request)
    if (
        not isinstance(pending, dict)
        or not code
        or not secrets.compare_digest(str(pending.get("state", "")), state)
    ):
        logger.warning("Sign-in callback with a missing or wrong state")
        return _home(ERROR_FAILED, request)
    try:
        identity = oauth.finish(
            code, _redirect_uri(request), str(pending["verifier"]), str(pending["nonce"])
        )
    except AuthError as exc:
        logger.warning("Sign-in failed: %s", exc)
        return _home(ERROR_FAILED, request)
    if not is_allowed(
        identity.email,
        identity.hosted_domain,
        settings.allowed_email_domains,
        settings.allowed_emails,
    ):
        logger.info("Sign-in refused: not a college account")
        return _home(ERROR_DOMAIN, request)
    request.session.pop(SESSION_ERROR, None)
    request.session[SESSION_USER] = {"email": identity.email, "name": identity.name}
    logger.info("Signed in %s", user_hash(identity.email, settings.session_secret or ""))
    return RedirectResponse("/", status_code=303)


@router.post("/auth/logout", status_code=204)
def logout(request: Request) -> Response:
    """Forget the signed-in person."""
    if login_required(request):
        request.session.clear()
    return Response(status_code=204)


@router.get("/api/me", response_model=MeResponse)
def me(request: Request) -> MeResponse:
    """Say whether login is needed and who is signed in (also reports a failed sign-in once)."""
    required = login_required(request)
    error = request.session.pop(SESSION_ERROR, None) if required else None
    user = current_user(request)
    signed_in = required and user is not None
    settings: Settings = request.app.state.settings
    return MeResponse(
        login_required=required,
        signed_in=signed_in,
        name=user.name if user and signed_in else "",
        email=user.email if user and signed_in else "",
        error=str(error) if error else "",
        allowed_domains=list(settings.allowed_email_domains),
    )
