"""Sign in once so Unidex can READ your Drive folders (it can never change them).

Before the first run, in Google Cloud Console (same project as the website login):
  1. APIs & Services > Library: enable "Google Drive API".
  2. APIs & Services > Credentials > Create credentials > OAuth client ID,
     type "Desktop app". Copy its id and secret into .env as DRIVE_CLIENT_ID and
     DRIVE_CLIENT_SECRET (never paste them into chat).
  3. OAuth consent screen: add the Google account that can see the drives as a test user.

Then run (from the project folder)::

    python scripts/drive_login.py

A browser opens; sign in with the account that can open the drives and allow read-only
access. A refresh token is saved to data/drive_token.json (git-ignored). While the OAuth
app is in "Testing" mode Google expires that token after 7 days; just run this again.
"""

import logging

from unidex.auth.drive import run_loopback_login, save_refresh_token
from unidex.config import load_settings
from unidex.exceptions import UnidexError
from unidex.logging_setup import configure_logging

logger = logging.getLogger("drive_login")


def main() -> int:
    """Run the one-time sign-in.

    Returns:
        The process exit code: 0 on success, 1 on a handled error.
    """
    try:
        settings = load_settings()
        configure_logging(settings.log_level)
        if not (settings.drive_client_id and settings.drive_client_secret):
            logger.error("Set DRIVE_CLIENT_ID and DRIVE_CLIENT_SECRET in .env first (see above).")
            return 1
        token = run_loopback_login(settings.drive_client_id, settings.drive_client_secret)
        save_refresh_token(settings.drive_token_path, token)
    except UnidexError as exc:
        logger.error("%s", exc)
        return 1
    logger.info("Saved the Drive sign-in to %s", settings.drive_token_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
