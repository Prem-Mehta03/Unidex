"""The abstraction every file listing implements."""

from abc import ABC, abstractmethod
from collections.abc import Iterator

from unidex.models.raw_file import RawFile


class FileSource(ABC):
    """Something that can list files: a CSV export today, the Drive API later.

    The sync job only depends on this interface, so swapping the CSV for the
    real Drive API (Stage 6) does not change the job at all.
    """

    @abstractmethod
    def fetch(self) -> Iterator[RawFile]:
        """Yield every file in the listing.

        Yields:
            One :class:`RawFile` per file.
        """
