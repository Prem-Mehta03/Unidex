"""A quick profile of what a listing contains, for sanity checks and reports."""

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

from unidex.models.raw_file import RawFile

_COURSE_FOLDER_DEPTH = 2


@dataclass(frozen=True, slots=True)
class FileSummary:
    """Headline numbers about a set of files.

    Attributes:
        total: Number of files.
        indexable: Files flagged as searchable study material.
        by_course_folder: File counts keyed by the first two path segments,
            e.g. ``"2-1 CDCs/M3"``.
        by_extension: File counts keyed by extension (``""`` for none).
    """

    total: int
    indexable: int
    by_course_folder: dict[str, int]
    by_extension: dict[str, int]


def summarize(files: Iterable[RawFile]) -> FileSummary:
    """Count files overall, per course folder and per extension.

    Args:
        files: The files to profile.

    Returns:
        A :class:`FileSummary`, with the count dictionaries sorted largest first.
    """
    total = 0
    indexable = 0
    folders: Counter[str] = Counter()
    extensions: Counter[str] = Counter()
    for f in files:
        total += 1
        indexable += int(f.is_indexable)
        segments = [s for s in f.path.split("/") if s]
        folders["/".join(segments[:_COURSE_FOLDER_DEPTH]) or "(root)"] += 1
        extensions[f.extension] += 1
    return FileSummary(
        total=total,
        indexable=indexable,
        by_course_folder=dict(folders.most_common()),
        by_extension=dict(extensions.most_common()),
    )
