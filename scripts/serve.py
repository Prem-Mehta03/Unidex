"""Start the Unidex web server.

Usage (from the repo root, after loading CSVs and running the extractor)::

    python scripts/serve.py
    python scripts/serve.py --port 8080 --reload

Then open http://127.0.0.1:8000 in a browser. The interactive API reference is
at http://127.0.0.1:8000/docs.

By default the server only listens on this computer (127.0.0.1). A web host needs
``--host 0.0.0.0``; the script refuses that unless Google login is fully configured, so the
site can never be opened to the internet without a login.
"""

import argparse
import logging
from collections.abc import Sequence

import uvicorn

from unidex.api.app import create_app
from unidex.config import load_settings
from unidex.exceptions import UnidexError
from unidex.logging_setup import configure_logging

logger = logging.getLogger("serve")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def main(argv: Sequence[str] | None = None) -> int:
    """Run the script.

    Args:
        argv: Command-line arguments; defaults to ``sys.argv[1:]``.

    Returns:
        Process exit code (0 on success, 1 on a handled error).
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=DEFAULT_HOST, help="address to listen on")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="port to listen on")
    args = parser.parse_args(argv)

    try:
        settings = load_settings()
        configure_logging(settings.log_level)
        if args.host not in LOCAL_HOSTS and not settings.login_configured:
            logger.error(
                "Refusing to listen on %s without Google login (set GOOGLE_CLIENT_ID, "
                "GOOGLE_CLIENT_SECRET and SESSION_SECRET).",
                args.host,
            )
            return 1
        app = create_app(settings=settings)
    except UnidexError as exc:
        logger.error("%s", exc)
        return 1

    logger.info("Open http://%s:%d in your browser (Ctrl+C to stop)", args.host, args.port)
    uvicorn.run(app, host=args.host, port=args.port, log_level=settings.log_level.lower())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
