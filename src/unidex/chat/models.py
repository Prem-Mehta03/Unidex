"""Data classes for the chat layer."""

from dataclasses import dataclass, field, replace
from enum import StrEnum


class Material(StrEnum):
    """Kinds of study material a student can ask for."""

    PAPERS = "papers"
    SOLUTIONS = "solutions"
    NOTES = "notes"
    SLIDES = "slides"
    TUTORIALS = "tutorials"
    ASSIGNMENTS = "assignments"
    LABS = "labs"


# Which stored document types each kind of material covers.
MATERIAL_DOC_TYPES: dict[Material, tuple[str, ...]] = {
    Material.PAPERS: ("pyq",),
    Material.SOLUTIONS: ("solution",),
    Material.NOTES: ("notes", "handout", "cheatsheet"),
    Material.SLIDES: ("slides",),
    Material.TUTORIALS: ("tutorial",),
    Material.ASSIGNMENTS: ("assignment",),
    Material.LABS: ("lab",),
}

MATERIAL_LABELS: dict[Material, str] = {
    Material.PAPERS: "Past papers",
    Material.SOLUTIONS: "Solutions",
    Material.NOTES: "Notes",
    Material.SLIDES: "Lecture slides",
    Material.TUTORIALS: "Tutorials",
    Material.ASSIGNMENTS: "Assignments",
    Material.LABS: "Lab material",
}

PAPER_MATERIALS = frozenset({Material.PAPERS, Material.SOLUTIONS})

# Used when a student names a course but not what they want.
DEFAULT_MATERIALS = (
    Material.PAPERS,
    Material.SOLUTIONS,
    Material.NOTES,
    Material.SLIDES,
    Material.TUTORIALS,
)

MAX_TOPICS = 8
MAX_TOPIC_LENGTH = 60
MAX_RECENT_YEARS = 10


@dataclass(frozen=True, slots=True)
class Interpretation:
    """What the student wants, as a small form.

    Attributes:
        courses: Course codes, e.g. ``("CS F213",)``.
        exam_types: Exam types for papers: ``midsem``, ``compre``, ``quiz``, ...
        materials: Kinds of material wanted.
        years: Academic start years asked for explicitly, e.g. ``(2022,)``.
        recent_years: "last N years" (counted from the newest year that has papers).
        topics: Topic phrases, e.g. ``("inheritance", "polymorphism")``.
        quiz_number: Quiz or test number, if one was named.
    """

    courses: tuple[str, ...] = ()
    exam_types: tuple[str, ...] = ()
    materials: tuple[Material, ...] = ()
    years: tuple[int, ...] = ()
    recent_years: int | None = None
    topics: tuple[str, ...] = ()
    quiz_number: int | None = None

    def is_blank(self) -> bool:
        """Return True when nothing was understood."""
        return not (
            self.courses
            or self.exam_types
            or self.materials
            or self.years
            or self.recent_years
            or self.topics
            or self.quiz_number
        )

    def with_changes(self, **changes: object) -> "Interpretation":
        """Return a copy with some fields replaced.

        Args:
            **changes: Field names and their new values.

        Returns:
            The updated copy.
        """
        return replace(self, **changes)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class ParseResult:
    """The outcome of reading one message.

    Attributes:
        interpretation: The merged form (previous form plus this message).
        understood: False when the message held nothing we could use.
        smalltalk: A canned reply for greetings and thanks, if that is all it was.
        used_llm: True when a language model helped.
        notes: Short facts worth telling the student ("I read '2023' as 2023-24").
    """

    interpretation: Interpretation
    understood: bool
    smalltalk: str = ""
    used_llm: bool = False
    notes: tuple[str, ...] = field(default_factory=tuple)
