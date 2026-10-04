"""The in-memory catalog: free-text ranking combined with metadata filters.

Why filters run *before* ranking
--------------------------------
BM25 only sees words. A query such as "midsem solutions oop" treats "oop" as one
more word, so a file about something else that happens to mention it can rank
first. Metadata filters fix that properly: the student (or, in Stage 5, the
chat) says "course = CS F213", and ranking then happens *inside* that set only.

Data structures used
--------------------
* A **filter index**: for every facet value (for example ``doc_type = pyq``) a
  set of document ids. Picking filters is set union (values of one facet) and
  set intersection (different facets), the same idea as combining posting
  lists in the inverted index. Intersecting the smallest set first keeps it fast.
* The BM25 index from Stage 2, called with ``allowed_ids`` so it never even
  scores documents the filters excluded.
"""

import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace

from unidex.db.repositories import DocumentView
from unidex.models.search import SearchDocument
from unidex.normalize import normalize_alias
from unidex.search.labels import (
    DOC_TYPE_LABELS,
    EXAM_TYPE_LABELS,
    EXAM_TYPES_SHOWN,
    academic_year_label,
)
from unidex.search.strategies import Bm25Search
from unidex.search.tokenizer import tokenize

FACETS = ("course", "doc_type", "exam_type", "year")
DEFAULT_MAX_HITS = 300
MAX_ALIAS_WORDS = 3  # longest course nickname we look for, e.g. "logic in cs"
_WORD = re.compile(r"[A-Za-z0-9]+")


@dataclass(frozen=True, slots=True)
class SearchFilters:
    """The filters a student has switched on.

    Several values of one facet mean "any of these" (OR); different facets
    combine with AND. An empty set means the facet is not filtered.

    Attributes:
        courses: Course codes such as ``CS F213``.
        doc_types: Document types such as ``pyq``.
        exam_types: Exam types such as ``midsem``.
        years: Academic start years such as ``2023``.
    """

    courses: frozenset[str] = frozenset()
    doc_types: frozenset[str] = frozenset()
    exam_types: frozenset[str] = frozenset()
    years: frozenset[int] = frozenset()

    def is_empty(self) -> bool:
        """Return True when no facet is filtered."""
        return not (self.courses or self.doc_types or self.exam_types or self.years)

    def values(self, facet: str) -> frozenset[str] | frozenset[int]:
        """Return the selected values of one facet.

        Args:
            facet: One of :data:`FACETS`.

        Returns:
            The selected values (empty when the facet is not filtered).

        Raises:
            ValueError: If ``facet`` is not a known facet name.
        """
        mapping: dict[str, frozenset[str] | frozenset[int]] = {
            "course": self.courses,
            "doc_type": self.doc_types,
            "exam_type": self.exam_types,
            "year": self.years,
        }
        if facet not in mapping:
            raise ValueError(f"Unknown facet {facet!r}")
        return mapping[facet]

    def without(self, facet: str) -> "SearchFilters":
        """Return a copy with one facet switched off.

        Args:
            facet: One of :data:`FACETS`.

        Returns:
            The filters minus ``facet``.
        """
        return SearchFilters(
            courses=frozenset() if facet == "course" else self.courses,
            doc_types=frozenset() if facet == "doc_type" else self.doc_types,
            exam_types=frozenset() if facet == "exam_type" else self.exam_types,
            years=frozenset() if facet == "year" else self.years,
        )


@dataclass(frozen=True, slots=True)
class Hit:
    """One matching document.

    Attributes:
        doc: The document and its metadata.
        score: BM25 score, or 0.0 when the search was filter-only.
        matched_terms: Query words found in the file name or folder path.
    """

    doc: DocumentView
    score: float
    matched_terms: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FacetOption:
    """One choice in a filter group.

    Attributes:
        value: Value sent back to the API when selected.
        label: Text shown to the student.
        count: How many documents would match if this option were added.
    """

    value: str
    label: str
    count: int


@dataclass(frozen=True, slots=True)
class CatalogResult:
    """Everything one search produces.

    Attributes:
        hits: Best matches, at most ``max_hits``.
        total: How many documents matched before the ``max_hits`` cut.
        facets: For each facet, the options with live counts.
        detected_courses: Course codes recognised in the query text (for example
            ``"oop"`` -> ``"CS F213"``) and applied as a course filter.
    """

    hits: list[Hit]
    total: int
    facets: dict[str, list[FacetOption]] = field(default_factory=dict)
    detected_courses: tuple[str, ...] = ()


class Catalog:
    """Searchable collection of documents with metadata filters."""

    def __init__(
        self,
        views: Sequence[DocumentView],
        course_aliases: Mapping[str, str] | None = None,
    ) -> None:
        """Build the BM25 index and the filter index (done once, at start-up).

        Args:
            views: Every document, typically from ``iter_document_views``.
            course_aliases: Course nicknames as ``{normalised alias: course code}``
                (see ``load_alias_map``). Used to turn "oop" in a query into a
                course filter. Course codes themselves always work.
        """
        self._views = list(views)
        documents = [
            SearchDocument(doc_id=i, title=v.name, path=v.path, url=v.url)
            for i, v in enumerate(self._views)
        ]
        self._bm25 = Bm25Search(documents)
        self._all_ids = frozenset(range(len(self._views)))
        self._course_names: dict[str, str] = {}
        self._index: dict[str, dict[str, set[int]]] = {facet: {} for facet in FACETS}
        for doc_id, view in enumerate(self._views):
            self._add_to_index(doc_id, view)
        self._aliases = self._usable_aliases(course_aliases or {})

    def _add_to_index(self, doc_id: int, view: DocumentView) -> None:
        """Register one document under each facet value it has."""
        if view.course_code:
            self._course_names.setdefault(view.course_code, view.course_name)
            self._index["course"].setdefault(view.course_code, set()).add(doc_id)
        self._index["doc_type"].setdefault(view.doc_type, set()).add(doc_id)
        if view.exam_type in EXAM_TYPES_SHOWN:
            self._index["exam_type"].setdefault(view.exam_type, set()).add(doc_id)
        if view.academic_year is not None:
            self._index["year"].setdefault(str(view.academic_year), set()).add(doc_id)

    def _usable_aliases(self, course_aliases: Mapping[str, str]) -> dict[str, str]:
        """Keep only nicknames of courses that have documents, and add course codes."""
        known = self._index["course"]
        usable = {alias: code for alias, code in course_aliases.items() if code in known}
        for code in known:
            usable.setdefault(normalize_alias(code), code)
        return usable

    def interpret(self, query: str) -> tuple[tuple[str, ...], str]:
        """Find course nicknames in a query and separate them from the other words.

        Looks at runs of up to three words, longest first, so "logic in cs" is
        one nickname rather than three words. "oop midsem solutions" becomes
        the course ``CS F213`` plus the text "midsem solutions".

        Args:
            query: What the student typed.

        Returns:
            ``(course codes found, the query without those words)``.
        """
        words = _WORD.findall(query)
        codes: list[str] = []
        kept: list[str] = []
        i = 0
        while i < len(words):
            for size in range(min(MAX_ALIAS_WORDS, len(words) - i), 0, -1):
                code = self._aliases.get(normalize_alias("".join(words[i : i + size])))
                if code is not None:
                    if code not in codes:
                        codes.append(code)
                    i += size
                    break
            else:
                kept.append(words[i])
                i += 1
        return tuple(codes), " ".join(kept)

    def __len__(self) -> int:
        """Return the number of documents in the catalog."""
        return len(self._views)

    def suggest(self, prefix: str, limit: int = 8) -> list[str]:
        """Autocomplete a partly typed word.

        Args:
            prefix: The partial word.
            limit: Maximum number of suggestions.

        Returns:
            Completions, most common first.
        """
        return self._bm25.suggest(prefix, limit=limit)

    def course_label(self, code: str) -> str:
        """Return the display label of a course, e.g. ``CS F213 · Object Oriented Programming``.

        Args:
            code: Course code.

        Returns:
            The label (just the code if the course name is unknown).
        """
        return self._label("course", code)

    def facet_options(self, facet: str) -> list[FacetOption]:
        """List the options of one facet across the whole catalog.

        Args:
            facet: One of :data:`FACETS`.

        Returns:
            Options with counts over all documents, in display order.
        """
        return self._options(facet, self._all_ids)

    def search(
        self,
        query: str,
        filters: SearchFilters | None = None,
        max_hits: int = DEFAULT_MAX_HITS,
        detect_courses: bool = True,
    ) -> CatalogResult:
        """Search by text, by filters, or both.

        * Text only: BM25 ranking over everything.
        * Filters only ("browse"): matching documents, newest year first.
        * Both: BM25 ranking inside the filtered set.
        * Neither: no hits (the facets still describe the whole catalog).

        A course nickname in the text ("oop") becomes a course filter, unless
        the student already picked a different course or ``detect_courses`` is
        False. If the nickname names a course the student already picked, the
        word is simply dropped from the text.

        Args:
            query: Free text; may be empty.
            filters: Facet filters; ``None`` means none.
            max_hits: Maximum number of hits returned.
            detect_courses: Whether to turn course nicknames in the text into a filter.

        Returns:
            Hits, the total match count and live facet counts.
        """
        active = filters or SearchFilters()
        detected: tuple[str, ...] = ()
        if detect_courses:
            found, remaining = self.interpret(query)
            if found and not active.courses:
                detected = found
                active = replace(active, courses=frozenset(found))
                query = remaining
            elif found and set(found) <= active.courses:
                query = remaining  # the student already picked this course; drop the redundant word
        has_text = bool(tokenize(query))
        if has_text:
            text_scores = {
                hit.doc_id: hit.score
                for hit in self._bm25.search(query, limit=len(self._views) or 1)
            }
            base_ids: frozenset[int] = frozenset(text_scores)
        else:
            base_ids = self._all_ids

        facets = {
            facet: self._options(facet, base_ids & self._allowed(active.without(facet)))
            for facet in FACETS
        }

        if not has_text and active.is_empty():
            return CatalogResult(hits=[], total=0, facets=facets, detected_courses=detected)

        allowed = self._allowed(active)
        matching = base_ids & allowed
        if has_text:
            ranked = self._bm25.search(query, limit=max_hits, allowed_ids=allowed)
            hits = [self._hit(r.doc_id, r.score, query) for r in ranked]
        else:
            newest_first = sorted(matching, key=self._browse_order)
            hits = [self._hit(doc_id, 0.0, "") for doc_id in newest_first[:max_hits]]
        return CatalogResult(
            hits=hits, total=len(matching), facets=facets, detected_courses=detected
        )

    def _browse_order(self, doc_id: int) -> tuple[int, str, str]:
        """Sort key for filter-only results: newest year first, then by path."""
        view = self._views[doc_id]
        year = view.academic_year if view.academic_year is not None else 0
        return (-year, view.path, view.name)

    def _hit(self, doc_id: int, score: float, query: str) -> Hit:
        """Build a :class:`Hit`, noting which query words the file matched."""
        view = self._views[doc_id]
        return Hit(doc=view, score=score, matched_terms=self._matched_terms(view, query))

    @staticmethod
    def _matched_terms(view: DocumentView, query: str) -> tuple[str, ...]:
        """Return the query words that occur in the file name or folder path."""
        if not query:
            return ()
        document_tokens = set(tokenize(f"{view.name} {view.path}"))
        found: list[str] = []
        for word in dict.fromkeys(tokenize(query, stem=False)):
            if any(token in document_tokens for token in tokenize(word)):
                found.append(word)
        return tuple(found)

    def _allowed(self, filters: SearchFilters) -> frozenset[int]:
        """Return the ids that satisfy every active facet.

        OR within a facet (union), AND across facets (intersection). The
        smallest set is intersected first so the work stays small.
        """
        groups: list[set[int]] = []
        for facet in FACETS:
            selected = filters.values(facet)
            if not selected:
                continue
            union: set[int] = set()
            for value in selected:
                union |= self._index[facet].get(str(value), set())
            groups.append(union)
        if not groups:
            return self._all_ids
        groups.sort(key=len)
        result = groups[0]
        for other in groups[1:]:
            result = result & other
            if not result:
                break
        return frozenset(result)

    def _options(self, facet: str, within: frozenset[int]) -> list[FacetOption]:
        """Build the options of a facet, counting only documents in ``within``."""
        counts: Counter[str] = Counter()
        for value, ids in self._index[facet].items():
            overlap = len(ids & within)
            if overlap:
                counts[value] = overlap
        options = [
            FacetOption(value=value, label=self._label(facet, value), count=count)
            for value, count in counts.items()
        ]
        return sorted(options, key=lambda option: self._option_order(facet, option))

    def _label(self, facet: str, value: str) -> str:
        """Return the display label of a facet value."""
        if facet == "course":
            name = self._course_names.get(value, "")
            return f"{value} · {name}" if name else value
        if facet == "doc_type":
            return DOC_TYPE_LABELS.get(value, value)
        if facet == "exam_type":
            return EXAM_TYPE_LABELS.get(value, value)
        return academic_year_label(int(value))

    @staticmethod
    def _option_order(facet: str, option: FacetOption) -> tuple[int, str]:
        """Sort options: years newest first, exam types in a fixed order, rest A-Z."""
        if facet == "year":
            return (-int(option.value), "")
        if facet == "exam_type":
            return (EXAM_TYPES_SHOWN.index(option.value), "")
        if facet == "doc_type":
            order = list(DOC_TYPE_LABELS)
            return (order.index(option.value) if option.value in order else len(order), "")
        return (0, option.label)


def parse_years(values: Iterable[str]) -> frozenset[int]:
    """Convert year strings to integers.

    Args:
        values: Strings such as ``"2023"``.

    Returns:
        The set of years.

    Raises:
        ValueError: If a value is not a four-digit year between 2000 and 2100.
    """
    years: set[int] = set()
    for text in values:
        year = int(text)
        if not 2000 <= year <= 2100:
            raise ValueError(f"Year out of range: {text}")
        years.add(year)
    return frozenset(years)
