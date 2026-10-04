"""Small, pure helpers for turning raw listing fields into clean values.

"Pure" means each function only looks at its arguments and returns a result,
with no database or network access. That makes them trivial to test.
"""

import re
from datetime import date, datetime
from pathlib import PurePosixPath

from unidex.exceptions import IngestionError

# Files worth searching: documents students read. Lab source code (.v, .py),
# simulation output (.vcd, .vvp), archives (.zip) and extension-less build
# files are stored as raw files but not indexed.
INDEXABLE_EXTENSIONS = frozenset({"pdf", "ppt", "pptx", "doc", "docx", "png", "jpg", "jpeg"})

# Google-native documents have no file extension, so they are recognised by MIME type.
INDEXABLE_MIME_TYPES = frozenset(
    {
        "application/vnd.google-apps.document",
        "application/vnd.google-apps.presentation",
    }
)

# Matches the file/folder id inside the common Drive and Docs URL shapes:
#   https://drive.google.com/file/d/<id>/view
#   https://docs.google.com/document/d/<id>/edit
#   https://drive.google.com/drive/folders/<id>
#   https://drive.google.com/open?id=<id>
_DRIVE_ID_PATTERN = re.compile(r"(?:/d/|/folders/|[?&]id=)([A-Za-z0-9_-]+)")

# The Apps Script export writes dates as month/day/year, e.g. "12/4/2025" is 4 December 2025.
_DATE_FORMAT = "%m/%d/%Y"


def extract_drive_file_id(url: str) -> str:
    """Pull the Google Drive file id out of a share link.

    Args:
        url: A Drive or Docs link.

    Returns:
        The id portion of the link.

    Raises:
        IngestionError: If no id can be found in the URL.
    """
    match = _DRIVE_ID_PATTERN.search(url)
    if match is None:
        raise IngestionError(f"Cannot find a Drive id in URL: {url!r}")
    return match.group(1)


def parse_modified_date(text: str) -> date | None:
    """Parse a month/day/year date string from the listing.

    Args:
        text: Text such as ``"12/4/2025"``; blank means "unknown".

    Returns:
        The date, or ``None`` if ``text`` is blank.

    Raises:
        IngestionError: If ``text`` is not a valid month/day/year date.
    """
    cleaned = text.strip()
    if not cleaned:
        return None
    try:
        return datetime.strptime(cleaned, _DATE_FORMAT).date()
    except ValueError as exc:
        raise IngestionError(f"Invalid date {text!r}; expected month/day/year") from exc


def file_extension(name: str) -> str:
    """Return a file name's extension in lower case, without the dot.

    Hidden files such as ``.DS_Store`` and names without a dot have no extension.

    Args:
        name: File name, e.g. ``"Midsem QP.PDF"``.

    Returns:
        ``"pdf"`` for the example above, or ``""`` when there is none.
    """
    return PurePosixPath(name).suffix.lstrip(".").lower()


def is_indexable(extension: str, mime_type: str) -> bool:
    """Decide whether a file looks like study material worth searching.

    Args:
        extension: Lower-case extension without the dot (may be empty).
        mime_type: MIME type reported by Drive.

    Returns:
        True for documents, slides and scans; False for code, archives and the like.
    """
    return extension in INDEXABLE_EXTENSIONS or mime_type in INDEXABLE_MIME_TYPES
