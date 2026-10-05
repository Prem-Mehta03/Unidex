"""Measure how well the chat reads messages, against a hand-written list.

A case file is a CSV with one message per row and the form a student would
expect to see (see ``eval/chat_messages.csv``). List columns use ``;`` between
values. ``materials`` may also be ``default`` (what we show when the student
names no material) or ``all``.

Scores:

* **Field accuracy**: for each of course, exam, material, year, topics and quiz
  number, how many cases match exactly.
* **Form accuracy**: how many cases match on *every* field.
* **Kind accuracy**: did the chat do the right thing (show a form, ask for the
  course, or reply to small talk)? Asking when the course is missing, rather
  than guessing, is counted here.
"""

import csv
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from unidex.chat.models import DEFAULT_MATERIALS, Interpretation, Material
from unidex.chat.service import ChatReply
from unidex.exceptions import IngestionError

FIELDS = ("courses", "exam_types", "materials", "years", "recent_years", "topics", "quiz_number")
REQUIRED_COLUMNS = ("message", "kind", *FIELDS)

Responder = Callable[[str, Interpretation | None], ChatReply]


@dataclass(frozen=True, slots=True)
class ChatCase:
    """One test message and the expected result.

    Attributes:
        message: What the student typed.
        previous_message: An earlier message in the same conversation, or empty.
        kind: Expected reply kind.
        expected: Expected value of each field (sets, or a number / None).
        note: Why the case exists.
    """

    message: str
    previous_message: str
    kind: str
    expected: dict[str, object]
    note: str


@dataclass(slots=True)
class CaseOutcome:
    """The result of one case.

    Attributes:
        case: The test case.
        actual: What the chat produced, per field.
        kind: The reply kind it produced.
        wrong_fields: Names of fields that did not match.
    """

    case: ChatCase
    actual: dict[str, object]
    kind: str
    wrong_fields: list[str] = field(default_factory=list)

    @property
    def kind_ok(self) -> bool:
        """True when the reply kind was the expected one."""
        return self.kind == self.case.kind

    @property
    def form_ok(self) -> bool:
        """True when every field matched."""
        return not self.wrong_fields


@dataclass(frozen=True, slots=True)
class ChatReport:
    """Scores over all cases.

    Attributes:
        outcomes: One outcome per case.
    """

    outcomes: list[CaseOutcome]

    @property
    def total(self) -> int:
        """Number of cases."""
        return len(self.outcomes)

    def field_correct(self, name: str) -> int:
        """Return how many cases got one field right."""
        return sum(1 for o in self.outcomes if name not in o.wrong_fields)

    @property
    def forms_correct(self) -> int:
        """Return how many cases matched on every field."""
        return sum(1 for o in self.outcomes if o.form_ok)

    @property
    def kinds_correct(self) -> int:
        """Return how many cases got the right reply kind."""
        return sum(1 for o in self.outcomes if o.kind_ok)

    @property
    def clarifications(self) -> tuple[int, int]:
        """Return ``(correctly asked, should have asked)`` for the course question."""
        should = [o for o in self.outcomes if o.case.kind == "clarify"]
        return sum(1 for o in should if o.kind_ok), len(should)


def _split(value: str) -> frozenset[str]:
    return frozenset(part.strip() for part in value.split(";") if part.strip())


def _materials(value: str) -> frozenset[str]:
    if value.strip() == "default":
        return frozenset(m.value for m in DEFAULT_MATERIALS)
    if value.strip() == "all":
        return frozenset(m.value for m in Material)
    return _split(value)


def load_cases(csv_path: Path) -> list[ChatCase]:
    """Read test messages from a CSV file.

    Args:
        csv_path: Path to the file.

    Returns:
        The cases, in file order.

    Raises:
        IngestionError: If the file is missing, lacks a required column, or has a bad value.
    """
    try:
        with csv_path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            missing = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
            if missing:
                raise IngestionError(f"{csv_path}: missing column(s): {', '.join(missing)}")
            rows = list(reader)
    except OSError as exc:
        raise IngestionError(f"Cannot read {csv_path}: {exc}") from exc
    cases: list[ChatCase] = []
    for number, row in enumerate(rows, start=2):
        try:
            expected: dict[str, object] = {
                "courses": _split(row["courses"]),
                "exam_types": _split(row["exam_types"]),
                "materials": _materials(row["materials"]),
                "years": frozenset(int(y) for y in _split(row["years"])),
                "recent_years": int(row["recent_years"]) if row["recent_years"].strip() else None,
                "topics": _split(row["topics"]),
                "quiz_number": int(row["quiz_number"]) if row["quiz_number"].strip() else None,
            }
        except ValueError as exc:
            raise IngestionError(f"{csv_path} line {number}: bad number ({exc})") from exc
        if row["kind"] not in {"confirm", "clarify", "smalltalk", "unclear"}:
            raise IngestionError(f"{csv_path} line {number}: unknown kind {row['kind']!r}")
        cases.append(
            ChatCase(
                message=row["message"],
                previous_message=row.get("previous_message", "") or "",
                kind=row["kind"],
                expected=expected,
                note=row.get("note", "") or "",
            )
        )
    return cases


def _actual(interpretation: Interpretation) -> dict[str, object]:
    return {
        "courses": frozenset(interpretation.courses),
        "exam_types": frozenset(interpretation.exam_types),
        "materials": frozenset(m.value for m in interpretation.materials),
        "years": frozenset(interpretation.years),
        "recent_years": interpretation.recent_years,
        "topics": frozenset(interpretation.topics),
        "quiz_number": interpretation.quiz_number,
    }


def evaluate(cases: list[ChatCase], respond: Responder) -> ChatReport:
    """Run every case through the chat.

    Args:
        cases: The test cases.
        respond: Function ``(message, previous form) -> ChatReply``.

    Returns:
        Scores and per-case outcomes.
    """
    outcomes: list[CaseOutcome] = []
    for case in cases:
        previous = (
            respond(case.previous_message, None).interpretation if case.previous_message else None
        )
        reply = respond(case.message, previous)
        actual = _actual(reply.interpretation)
        wrong = [name for name in FIELDS if actual[name] != case.expected[name]]
        if case.kind == "smalltalk":
            wrong = []  # small talk has no form to compare
        outcomes.append(CaseOutcome(case=case, actual=actual, kind=reply.kind, wrong_fields=wrong))
    return ChatReport(outcomes)
