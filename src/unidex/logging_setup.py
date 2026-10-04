"""Logging configuration.

Unidex never uses ``print()``. Every module creates its own logger with
``logging.getLogger(__name__)`` and the entry points (scripts, the API) call
:func:`configure_logging` once at start-up to decide where messages go.
"""

import logging

_HANDLER_NAME = "unidex-console"
_LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def configure_logging(level: str = "INFO") -> None:
    """Send log records to the console (stderr) at the given level.

    Safe to call more than once: the console handler is installed only the
    first time, later calls just update the level.

    Args:
        level: A standard level name such as ``"DEBUG"`` or ``"INFO"``.
    """
    root = logging.getLogger()
    root.setLevel(level)
    if any(handler.get_name() == _HANDLER_NAME for handler in root.handlers):
        return
    handler = logging.StreamHandler()
    handler.set_name(_HANDLER_NAME)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    root.addHandler(handler)
