"""Compare a fresh Drive listing with an older listing (for example the CSV export).

Used by ``scripts/sync_drive.py --compare`` as a sanity check before trusting the Drive
API: if the same files show up with the same folder paths, the two listings agree.
"""

from collections.abc import Iterable
from dataclasses import dataclass

from unidex.models.raw_file import RawFile

EXAMPLES = 3


@dataclass(frozen=True, slots=True)
class Comparison:
    """How two listings differ.

    Attributes:
        in_both: Files present in both listings (matched by Drive id).
        same_path: Of those, how many have the same folder path and name.
        only_new: Files only in the new listing.
        only_old: Files only in the old listing.
        path_examples: Up to a few ``(old path, new path)`` pairs that differ.
        only_new_examples: A few paths that are only in the new listing.
        only_old_examples: A few paths that are only in the old listing.
    """

    in_both: int
    same_path: int
    only_new: int
    only_old: int
    path_examples: tuple[tuple[str, str], ...]
    only_new_examples: tuple[str, ...]
    only_old_examples: tuple[str, ...]


def compare_listings(new: Iterable[RawFile], old: Iterable[RawFile]) -> Comparison:
    """Compare two listings by Drive file id.

    Args:
        new: The fresh listing (for example from the Drive API).
        old: The earlier listing (for example the CSV export).

    Returns:
        Counts and a few examples of every kind of difference.
    """
    new_by_id = {f.drive_file_id: f for f in new}
    old_by_id = {f.drive_file_id: f for f in old}
    both = new_by_id.keys() & old_by_id.keys()
    same = 0
    different: list[tuple[str, str]] = []
    for file_id in sorted(both):
        a, b = old_by_id[file_id], new_by_id[file_id]
        if (a.path, a.name) == (b.path, b.name):
            same += 1
        elif len(different) < EXAMPLES:
            different.append((f"{a.path}/{a.name}", f"{b.path}/{b.name}"))
    only_new = sorted(new_by_id.keys() - old_by_id.keys())
    only_old = sorted(old_by_id.keys() - new_by_id.keys())
    return Comparison(
        in_both=len(both),
        same_path=same,
        only_new=len(only_new),
        only_old=len(only_old),
        path_examples=tuple(different),
        only_new_examples=tuple(
            f"{new_by_id[i].path}/{new_by_id[i].name}" for i in only_new[:EXAMPLES]
        ),
        only_old_examples=tuple(
            f"{old_by_id[i].path}/{old_by_id[i].name}" for i in only_old[:EXAMPLES]
        ),
    )
