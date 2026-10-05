"""Read a chat message with plain rules (no language model, no network).

The parser looks for six things, in this order: course, years, exam, kind of
material, quiz number, and finally whatever words are left over, which become
topics. Every recognised phrase is cut out of the text, so it cannot also show
up as a topic.

Messages after the first are *refinements*: "show only 2022 solutions" changes
the previous form instead of starting again. Fields the new message mentions
replace the old values, unless the message says "also" / "include" / "add", in
which case the new values are added to the old ones.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from unidex.chat.models import (
    DEFAULT_MATERIALS,
    MAX_RECENT_YEARS,
    MAX_TOPIC_LENGTH,
    MAX_TOPICS,
    Interpretation,
    Material,
    ParseResult,
)

T = TypeVar("T")

MAX_MESSAGE_LENGTH = 500
_GAP = " | "
_NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}

ScanCourses = Callable[[str], tuple[tuple[str, ...], str]]


@dataclass(frozen=True, slots=True)
class _Rule:
    """A phrase pattern and the value it stands for."""

    pattern: re.Pattern[str]
    value: str


def _rules(*pairs: tuple[str, str]) -> list[_Rule]:
    """Compile (regex, value) pairs, case-insensitively."""
    return [_Rule(re.compile(rf"\b(?:{regex})\b", re.IGNORECASE), value) for regex, value in pairs]


# Order matters: longer phrases first ("lab compre" before "compre").
_EXAM_RULES = _rules(
    (r"lab[\s-]*compre(?:hensive)?s?", "lab_compre"),
    (r"lab[\s-]*(?:quiz(?:zes)?|test)s?", "lab_quiz"),
    (r"compre(?:hensive)?s?|end[\s-]*sem(?:ester)?s?|finals?", "compre"),
    (r"mid[\s-]*sem(?:ester)?s?|mid[\s-]*terms?", "midsem"),
    (r"quiz(?:zes)?", "quiz"),
    (r"tests?", "test"),
)

_MATERIAL_RULES = _rules(
    (r"everything|all\s+(?:the\s+)?(?:material|materials|resources|files|stuff)", "all"),
    (r"solutions?|sols?|solns?|answer[\s-]*keys?|answers?|marking\s+schemes?", Material.SOLUTIONS),
    (
        r"pyqs?|past\s+(?:year\s+)?papers?|previous\s+year(?:'?s)?(?:\s+(?:question\s+)?papers?)?"
        r"|prev\s+year(?:\s+papers?)?|old\s+papers?|question\s+papers?|papers?|qps?",
        Material.PAPERS,
    ),
    (r"notes?|handouts?|cheat[\s-]*sheets?|formula\s+sheets?", Material.NOTES),
    (r"slides?|lectures?|ppts?|presentations?", Material.SLIDES),
    (
        r"tutorials?|tuts?|practice\s+(?:problems?|questions?|sets?)|problem\s+sets?|exercises?",
        Material.TUTORIALS,
    ),
    (r"assignments?", Material.ASSIGNMENTS),
    (r"labs?", Material.LABS),
)

_RECENT = re.compile(
    r"\b(?:last|past|recent|latest|previous)\s+(\d+|one|two|three|four|five|six)\s+years?\b",
    re.IGNORECASE,
)
_LAST_YEAR = re.compile(r"\b(?:last|latest|most\s+recent)\s+year(?:'?s)?\b", re.IGNORECASE)
_LATEST = re.compile(r"\b(?:latest|newest|most\s+recent|recent)\b", re.IGNORECASE)
_SPAN = re.compile(r"\b(20\d{2})\s*[-\u2013/]\s*(\d{2}|\d{4})\b")
_SHORT_SPAN = re.compile(r"\b(\d{2})\s*[-\u2013]\s*(\d{2})\b")
_YEAR = re.compile(r"\b(20\d{2})\b")
_QUIZ_NUMBER = re.compile(r"\b(?:quiz(?:zes)?|test|q)\s*#?\s*(\d{1,2})\b", re.IGNORECASE)

_ADDITIVE = re.compile(r"\b(?:also|include|including|add|as\s+well|too|plus)\b", re.IGNORECASE)
_RESET = re.compile(r"\b(?:start\s+over|new\s+search|reset|clear\s+everything)\b", re.IGNORECASE)
_SPLIT = re.compile(r"\||,|;|&|\.|\?|!|\band\b|\bor\b|\bplus\b|\bthen\b", re.IGNORECASE)
_TOKEN = re.compile(r"[a-z0-9][a-z0-9'+#-]*")

_FILLER_WORDS = """
    i im ive id me my we our you your us a an the this that these those it its
    have has had having need needs needed want wants wanted looking look find finding get getting
    give show see search check fetch send pull bring list please pls kindly
    can could would will should may might do does did is are was were be been am
    for to of in on at by with from about around regarding related relating covering cover covers
    specifically especially particularly mainly mostly only just also include including add as well
    some any all every each more most other another new latest recent
    exam exams examination preparation prepare preparing prep study studying revise revision
    material materials resource resources file files stuff things thing doc docs document documents
    topic topics chapter chapters unit units part parts section sections
    last past previous year years upcoming next coming
    there here where which what who how when why if so but not no yes ok okay
    hi hello hey thanks thank
    """
_FILLER = frozenset(re.findall(r"[a-z]+", _FILLER_WORDS))

_SMALLTALK: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"^\s*(?:hi+|hello+|hey+|hola|yo|good\s+(?:morning|evening|afternoon))\W*$", re.I
        ),
        (
            "Hey there! \U0001f44b Tell me what you are preparing for, like "
            "“OOP midsem papers and notes”, and I will dig through the drives for you."
        ),
    ),
    (
        re.compile(r"^\s*(?:thanks?|thank\s+you|thx|ty|cheers|great|awesome|nice)\W*$", re.I),
        "You're welcome! Ask me anything else about the drives.",
    ),
    (
        re.compile(r"^\s*(?:help|\?|what\s+can\s+you\s+do|who\s+are\s+you)\W*$", re.I),
        "I find past papers, solutions, notes and slides in the department drives. "
        "Tell me the course, the exam, and the kind of material, "
        "for example “DD compre papers from the last 3 years”.",
    ),
]


class RuleParser:
    """Turn a message into an :class:`Interpretation` using rules only."""

    def __init__(self, scan_courses: ScanCourses) -> None:
        """Create a parser.

        Args:
            scan_courses: Finds course names, nicknames and codes in text; see
                ``Catalog.scan_courses``.
        """
        self._scan_courses = scan_courses

    def parse(self, message: str, previous: Interpretation | None = None) -> ParseResult:
        """Read one message.

        Args:
            message: What the student typed.
            previous: The form from earlier in the conversation, if any.

        Returns:
            The merged form and whether anything useful was understood.
        """
        text = message.strip()[:MAX_MESSAGE_LENGTH]
        for pattern, reply in _SMALLTALK:
            if pattern.match(text):
                return ParseResult(previous or Interpretation(), understood=True, smalltalk=reply)

        notes: list[str] = []
        courses, marked = self._scan_courses(text)
        marked = _RESET.sub(_GAP, marked)
        marked, years, recent = _take_years(marked, notes)
        marked, exams = _take_exams(marked)
        marked, materials = _take_materials(marked)
        quiz_number = _take_quiz_number(text, exams)
        topics = _take_topics(marked)

        fresh = Interpretation(
            courses=courses,
            exam_types=exams,
            materials=materials,
            years=years,
            recent_years=recent,
            topics=topics,
            quiz_number=quiz_number,
        )
        base = None if previous is None or _RESET.search(text) else previous
        merged = _merge(base, fresh, additive=bool(_ADDITIVE.search(text)))
        return ParseResult(
            interpretation=_with_defaults(merged),
            understood=not fresh.is_blank(),
            notes=tuple(notes),
        )


def _blank_out(pattern: re.Pattern[str], text: str, found: list[re.Match[str]]) -> str:
    """Replace every match of ``pattern`` by a gap and remember the matches."""

    def take(match: re.Match[str]) -> str:
        found.append(match)
        return _GAP

    return pattern.sub(take, text)


def _take_years(text: str, notes: list[str]) -> tuple[str, tuple[int, ...], int | None]:
    """Cut out year expressions; return the rest, explicit years and "last N"."""
    recent: int | None = None
    found: list[re.Match[str]] = []
    text = _blank_out(_RECENT, text, found)
    if found:
        raw = found[0].group(1).lower()
        count = _NUMBER_WORDS.get(raw) or int(raw)
        recent = max(1, min(count, MAX_RECENT_YEARS))
    else:
        text = _blank_out(_LAST_YEAR, text, found)
        if not found:
            text = _blank_out(_LATEST, text, found)
            if found:
                notes.append("I read “latest” as the newest year that has papers for this course.")
        if found:
            recent = 1

    years: list[int] = []
    spans: list[re.Match[str]] = []
    text = _blank_out(_SPAN, text, spans)
    for match in spans:
        years.append(int(match.group(1)))

    def take_short_span(match: re.Match[str]) -> str:
        first, second = int(match.group(1)), int(match.group(2))
        if second != (first + 1) % 100:
            return match.group(0)  # something like "10-15": not a year span, leave it
        years.append(2000 + first)
        return _GAP

    text = _SHORT_SPAN.sub(take_short_span, text)
    singles: list[re.Match[str]] = []
    text = _blank_out(_YEAR, text, singles)
    for match in singles:
        year = int(match.group(1))
        years.append(year)
        notes.append(f"I read “{year}” as the academic year {year}-{(year + 1) % 100:02d}.")
    unique = tuple(dict.fromkeys(years))
    return text, unique, recent


def _take_exams(text: str) -> tuple[str, tuple[str, ...]]:
    """Cut out exam words; return the rest and the exam types in order of appearance."""
    exams: list[str] = []
    for rule in _EXAM_RULES:
        found: list[re.Match[str]] = []
        text = _blank_out(rule.pattern, text, found)
        if found:
            exams.append(rule.value)
    return text, tuple(exams)


def _take_materials(text: str) -> tuple[str, tuple[Material, ...]]:
    """Cut out material words; return the rest and the kinds of material."""
    materials: list[Material] = []
    for rule in _MATERIAL_RULES:
        found: list[re.Match[str]] = []
        text = _blank_out(rule.pattern, text, found)
        if not found:
            continue
        if rule.value == "all":
            materials.extend(Material)
        else:
            materials.append(Material(rule.value))
    return text, tuple(dict.fromkeys(materials))


def _take_quiz_number(original: str, exams: tuple[str, ...]) -> int | None:
    """Find "quiz 2" / "test 1" / "q3" in the original text."""
    if not any(exam in {"quiz", "test", "lab_quiz"} for exam in exams) and not re.search(
        r"\bq\s*\d", original, re.IGNORECASE
    ):
        return None
    match = _QUIZ_NUMBER.search(original)
    return int(match.group(1)) if match else None


def _take_topics(text: str) -> tuple[str, ...]:
    """Turn the words left over after everything else was cut out into topic phrases."""
    topics: list[str] = []
    for piece in _SPLIT.split(text):
        tokens = _TOKEN.findall(piece.lower())
        while tokens and tokens[0] in _FILLER:
            tokens.pop(0)
        while tokens and tokens[-1] in _FILLER:
            tokens.pop()
        phrase = " ".join(tokens)
        if len(phrase) < 2 or len(phrase) > MAX_TOPIC_LENGTH or phrase in topics:
            continue
        topics.append(phrase)
    return tuple(topics[:MAX_TOPICS])


def _pick(old: tuple[T, ...], new: tuple[T, ...], *, additive: bool) -> tuple[T, ...]:
    """Choose between old and new values of a field.

    A field the new message did not mention keeps its old value. A mentioned
    field replaces the old value, or is added to it when the student said "also".
    """
    if not new:
        return old
    return tuple(dict.fromkeys(old + new)) if additive else new


def _merge(base: Interpretation | None, fresh: Interpretation, *, additive: bool) -> Interpretation:
    """Combine the previous form with what the new message said."""
    if base is None:
        return fresh
    if fresh.years or fresh.recent_years:
        years = (
            _pick(base.years, fresh.years, additive=additive)
            if fresh.years
            else (base.years if additive else ())
        )
        recent = (
            fresh.recent_years if fresh.recent_years else (base.recent_years if additive else None)
        )
    else:
        years, recent = base.years, base.recent_years
    return Interpretation(
        courses=_pick(base.courses, fresh.courses, additive=additive),
        exam_types=_pick(base.exam_types, fresh.exam_types, additive=additive),
        materials=_pick(base.materials, fresh.materials, additive=additive),
        years=years,
        recent_years=recent,
        topics=_pick(base.topics, fresh.topics, additive=additive),
        quiz_number=fresh.quiz_number if fresh.quiz_number is not None else base.quiz_number,
    )


def _with_defaults(interpretation: Interpretation) -> Interpretation:
    """Fill in sensible defaults for fields the student left out."""
    result = interpretation
    if result.quiz_number is not None and not result.exam_types:
        result = result.with_changes(exam_types=("quiz",))
    if not result.materials and (result.courses or result.exam_types or result.years):
        result = result.with_changes(materials=DEFAULT_MATERIALS)
    return result
