"""The raw file record produced by ingestion."""

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True, slots=True)
class RawFile:
    """One file exactly as found in a drive listing, before any interpretation.

    Attributes:
        drive_file_id: Google Drive's stable ID for the file. Unlike the path
            or name, it does not change when the file is moved or renamed.
        path: Folder path of the file, e.g. ``/2-1 CDCs/M3/Sem 1 25-26 (X)/Evals``.
        name: File name including extension.
        extension: Lower-case extension without the dot, or ``""`` if none.
        mime_type: MIME type reported by Drive.
        modified_on: Last-modified date, or ``None`` if the listing had none.
        url: Link that opens the file in Drive.
        is_indexable: Whether this looks like study material worth searching
            (as opposed to lab source code, build artefacts, archives, ...).
    """

    drive_file_id: str
    path: str
    name: str
    extension: str
    mime_type: str
    modified_on: date | None
    url: str
    is_indexable: bool
