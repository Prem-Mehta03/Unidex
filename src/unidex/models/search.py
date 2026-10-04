"""Data classes used by the search layer."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SearchDocument:
    """One searchable file.

    Attributes:
        doc_id: Identifier used inside the search index.
        title: File name, e.g. ``"Midsem Solutions.pdf"``.
        path: Folder path, e.g. ``"/2-1 CDCs/M3/Sem 1 25-26 (X)/Evals"``.
        url: Link that opens the file in Drive.
    """

    doc_id: int
    title: str
    path: str
    url: str

    @property
    def text(self) -> str:
        """Return the text that is indexed: the file name plus its folder path.

        Folder names carry a lot of meaning here ("Midsem", "Slides", the
        semester and the course nickname), so they are searchable too.
        """
        return f"{self.title} {self.path}"


@dataclass(frozen=True, slots=True)
class ScoredDoc:
    """A search result: a document id and how well it matched.

    Attributes:
        doc_id: Identifier of the matching document.
        score: Relevance score; higher is better. Scores from different
            strategies are not comparable with each other.
    """

    doc_id: int
    score: float
