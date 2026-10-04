"""Text normalisation shared by the database and ingestion layers."""

import re

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize_alias(text: str) -> str:
    """Reduce a course name or code to a canonical lookup key.

    Lower-cases and removes everything except letters and digits, so that
    ``"CS F213"``, ``"csf213"`` and ``"CS-F213"`` all map to ``"csf213"``, and
    ``"Logic in CS"`` maps to ``"logicincs"``.

    Args:
        text: Raw text typed by a student or found in a folder name.

    Returns:
        The normalised key (possibly empty if ``text`` had no letters or digits).
    """
    return _NON_ALNUM.sub("", text.lower())
