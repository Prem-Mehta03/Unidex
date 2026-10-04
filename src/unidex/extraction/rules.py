"""Rule-based metadata extraction.

Rules run first and cost nothing. Each decision carries a confidence:

* 0.95: an explicit keyword in the file name ("Midsem", "Quiz 2", "Handout").
* 0.85-0.9: the folder says it ("Quizzes", "Slides") or a clear name pattern.
* 0.6 or less: a guess (a "Test 1" treated as a quiz, or a name that mentions
  two different exams).

The overall confidence of a file is the weakest decision that matters for its
document type, so one shaky guess is enough to send a file to the review queue.
"""

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass

from unidex.extraction.path_parser import ParsedPath, parse_path
from unidex.models.enums import DocType, ExamType, ExtractionMethod, SyllabusScope
from unidex.models.metadata import ExtractedMetadata
from unidex.models.raw_file import RawFile

logger = logging.getLogger(__name__)

CONF_NAME = 0.95
CONF_FOLDER = 0.85
CONF_PATTERN = 0.9
CONF_GUESS = 0.6
CONF_CONFLICT = 0.55
MIN_YEAR_PREFIX = 10
MAX_YEAR_PREFIX = 40

_SEPARATORS = re.compile(r"[_\-+()\[\]#.,]+")
_SPACES = re.compile(r"\s+")

# --- exam keywords (matched against the lower-cased, separator-free name) ---
_LAB_COMPRE = re.compile(r"lab ?compre")
_LAB_QUIZ = re.compile(r"lab ?(?:quiz|test)")
_COMPRE = re.compile(r"compre")
_MIDSEM = re.compile(r"mid ?(?:sem|term|test|exam)")
_QUIZ = re.compile(r"quiz")
_TEST = re.compile(r"(?:^|\s)test ?\d|^t\d(?!\d)")
_NUMBER_AFTER_QUIZ = re.compile(r"(?:quiz|test) ?\D{0,4}?(\d{1,2})(?!\d)")
_NUMBER_T = re.compile(r"^t(\d)(?!\d)")
_NUMBER_Q = re.compile(r"\bq ?(\d{1,2})(?!\d)")
_NUMBER_LQ = re.compile(r"\blq ?(\d{1,2})(?!\d)")
_NUMBER_LAST = re.compile(r"(\d{1,2})$")
_MAKEUP = re.compile(r"make ?up|\bre ?(?:mid|compre|quiz|test)")

# --- document keywords ---
_STRONG_SOLUTION = re.compile(r"soln|answer ?key|\bans\b|\banswers\b|\bsol\b")
# "Series Solution of ODE" is a maths topic, not an answer file, hence the lookahead.
_WEAK_SOLUTION = re.compile(r"solutions?(?! of\b)")
# "Key" alone means an answer key only next to an exam word ("Quiz 1 Key").
_EXAM_KEY = re.compile(r"\bkey\b")
_WITH_SOLUTION = re.compile(r"\bwith (?:the )?(?:answer ?key|solutions?|answers?|soln)")
_GRADE_NAME = re.compile(
    r"av details|average|\bmarks\b|marks distribution|hist\b|distribution|results?\b"
)
_HANDOUT = re.compile(r"handout")
_CHEAT = re.compile(r"cheat ?sheet|formula ?sheet")
_ASSIGNMENT = re.compile(r"homework|\bhw\b|assignment")
_TUTORIAL = re.compile(r"tutorial|\btuts?\b|problem ?sheet")
_SLIDES_NAME = re.compile(r"slides?|lect(?:ure)? ?\d|^lec ?\d")
_NOTES_NAME = re.compile(r"notes|guide")

# --- folder keywords (matched against each folder name, lower-cased) ---
_F_GRADE = re.compile(r"insights|marks distribution")
_F_CHEAT = re.compile(r"cheat ?sheets?")
_F_TEXTBOOK = re.compile(r"^textbooks?$")
_F_SOLUTION = re.compile(r"^(?:hw ?)?(?:soln|solutions?)$")
_F_WITH_SOLUTION = re.compile(r"with solutions?")
_F_ASSIGNMENT = re.compile(r"homework|^hw\b|assignment")
_F_TUTORIAL = re.compile(r"tutorials?|^tuts$|problem sheets?")
_F_LAB = re.compile(r"^labs?\b|\blabs?$")
_F_SLIDES = re.compile(r"^slides?\b|^lecture slides")
_F_NOTES = re.compile(r"notes|guides?$|bootcamp")
_F_PAST = re.compile(r"past|archive|old")
_F_PRE_MID = re.compile(r"pre[\s_-]*mid")
_F_POST_MID = re.compile(r"post[\s_-]*mid")

_F_EXAMS: tuple[tuple[re.Pattern[str], ExamType], ...] = (
    (re.compile(r"lab ?compre"), ExamType.LAB_COMPRE),
    (re.compile(r"^lab ?(?:quiz|tests?)"), ExamType.LAB_QUIZ),
    (re.compile(r"^(?:re-? ?)?mid ?(?:sem|term)"), ExamType.MIDSEM),
    (re.compile(r"^(?:re-? ?)?compre"), ExamType.COMPRE),
    (re.compile(r"^quiz"), ExamType.QUIZ),
    (re.compile(r"^tests?\b"), ExamType.TEST),
)

# Document types whose exam type we try to work out. Slides, notes, tutorials
# and the like are not tied to one exam, so they get ExamType.NONE.
EXAM_RELEVANT = frozenset(
    {DocType.PYQ, DocType.SOLUTION, DocType.GRADE_STATS, DocType.CHEATSHEET, DocType.UNKNOWN}
)
# For these, "no exam found" simply means the file is not about one exam.
_NO_EXAM_NEEDED = frozenset({DocType.GRADE_STATS, DocType.CHEATSHEET})

# --- years in file names ---
_SPAN = re.compile(r"(?<!\d)(\d{2}) ?[-_/] ?(\d{2})(?!\d)")
_LONG_SPAN = re.compile(r"(?<!\d)20(\d{2})[\s_]+(\d{2})(?!\d)")
_GLUED_SPAN = re.compile(r"(?<!\d)(\d{2})(\d{2})(?!\d)")
_SINGLE_YEAR = re.compile(r"(?<!\d)(20\d{2})(?!\d)")
# "Lecture-16-17" is a lecture range, not the academic year 16-17.
_RANGE_PREFIX = re.compile(
    r"(?:lect(?:ure)?s?|lec|week|chapter|ch|unit|tut(?:orial)?|sheet|hw|problem|part|set|q)"
    r"[\s_-]*$",
    re.IGNORECASE,
)
# BITS student ids look like 2020A7PS0114G; a file named after one is a student's script.
_STUDENT_ID = re.compile(r"20\d{2}[a-z]\d[a-z]{2}\d{4}[a-z]", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class _Verdict:
    """One decision with its confidence and a short reason."""

    doc_type: DocType
    confidence: float
    reason: str


def _normalise(text: str) -> str:
    """Lower-case text and turn separators into single spaces."""
    return _SPACES.sub(" ", _SEPARATORS.sub(" ", text.lower())).strip()


def _stem(name: str, extension: str) -> str:
    """Return a file name without its extension."""
    suffix = f".{extension}"
    return name[: -len(suffix)] if extension and name.lower().endswith(suffix) else name


def _span_year(first: int, second: int) -> int | None:
    """Return the start year if two numbers form an academic year like 25-26."""
    if MIN_YEAR_PREFIX <= first <= MAX_YEAR_PREFIX and (first + 1) % 100 == second:
        return 2000 + first
    return None


def year_from_name(stem: str) -> tuple[int | None, bool]:
    """Find an academic year in a file name.

    Args:
        stem: File name without extension, in its original spelling.

    Returns:
        ``(year, is_span)``. ``is_span`` is true for explicit spans such as
        ``"21-22"`` (strong evidence) and false for a lone year such as
        ``"2018"`` (weak: it may be the calendar year the exam was held).
    """
    if _STUDENT_ID.search(stem):
        return None, False

    def usable(match: re.Match[str]) -> bool:
        return _RANGE_PREFIX.search(stem[: match.start()]) is None

    for match in _SPAN.finditer(stem):
        year = _span_year(int(match[1]), int(match[2]))
        if year is not None and usable(match):
            return year, True
    long_span = _LONG_SPAN.search(stem)
    if long_span is not None and usable(long_span):
        year = _span_year(int(long_span[1]), int(long_span[2]))
        if year is not None:
            return year, True
    for match in _GLUED_SPAN.finditer(stem):
        year = _span_year(int(match[1]), int(match[2]))
        if year is not None and usable(match):
            return year, True
    single = _SINGLE_YEAR.search(stem)
    if single is not None and usable(single):
        return int(single[1]), False
    return None, False


class RuleExtractor:
    """Interprets a :class:`RawFile` using keyword and folder rules."""

    def __init__(self, resolve_course: Callable[[str], int | None]) -> None:
        """Create an extractor.

        Args:
            resolve_course: Maps a folder name such as ``"OOP"`` to a course id.
                In production this is ``CourseRepository.resolve``; tests pass
                a plain function.
        """
        self._resolve_course = resolve_course

    def extract(self, file: RawFile) -> ExtractedMetadata:
        """Work out everything the rules can say about one file.

        Args:
            file: The raw file from the drive listing.

        Returns:
            The extracted metadata. Fields the rules cannot decide are left
            ``UNKNOWN`` or ``None`` and the confidence reflects that.
        """
        parsed = parse_path(file.path, self._resolve_course)
        stem = _stem(file.name, file.extension)
        name = _normalise(stem)
        folders = tuple(folder.lower().strip() for folder in parsed.category_folders)
        notes: list[str] = []

        verdict = self._doc_type(name, folders)
        doc_type = verdict.doc_type
        confidence = verdict.confidence
        notes.append(verdict.reason)

        exam_type = ExamType.NONE
        if doc_type in EXAM_RELEVANT:
            exam_type, exam_conf, exam_note = self._exam_type(name, folders)
            no_exam_expected = doc_type in _NO_EXAM_NEEDED or self._is_coursework(name, folders)
            if exam_type is ExamType.UNKNOWN and no_exam_expected:
                exam_type, exam_conf, exam_note = ExamType.NONE, confidence, ""
            if doc_type in (DocType.PYQ, DocType.SOLUTION, DocType.UNKNOWN):
                confidence = min(confidence, exam_conf)
            notes.append(exam_note)

        numbered = (ExamType.QUIZ, ExamType.TEST, ExamType.LAB_QUIZ)
        exam_number = self._exam_number(name, folders) if exam_type in numbered else None

        year, year_note = self._academic_year(parsed, stem, folders)
        if year_note:
            notes.append(year_note)
            if "differs" in year_note:
                confidence = min(confidence, CONF_CONFLICT + 0.1)

        if _STUDENT_ID.search(stem):
            confidence = min(confidence, CONF_CONFLICT)
            notes.append("file name is a student id; confirm it should be shown")

        if parsed.course_id is None:
            confidence = 0.0
            notes.append("course folder not recognised")

        return ExtractedMetadata(
            course_id=parsed.course_id,
            academic_year=year,
            semester=parsed.semester,
            instructor=parsed.instructor,
            exam_type=exam_type,
            exam_number=exam_number,
            doc_type=doc_type,
            is_makeup=bool(_MAKEUP.search(name))
            or any(_MAKEUP.search(_normalise(folder)) for folder in folders),
            has_solution=self._has_solution(name, folders),
            syllabus_scope=self._scope(folders),
            title=_SPACES.sub(" ", stem.replace("_", " ")).strip() or file.name,
            confidence=round(confidence, 2),
            method=ExtractionMethod.RULE,
            notes=tuple(note for note in notes if note),
        )

    # ------------------------------------------------------------------ doc type

    def _doc_type(self, name: str, folders: tuple[str, ...]) -> _Verdict:
        """Pick the document type with the first rule that matches."""

        def in_folders(pattern: re.Pattern[str]) -> bool:
            return any(pattern.search(folder) for folder in folders)

        if in_folders(_F_GRADE) or _GRADE_NAME.search(name):
            return _Verdict(DocType.GRADE_STATS, CONF_NAME, "marks / grade statistics")
        if _HANDOUT.search(name):
            return _Verdict(DocType.HANDOUT, CONF_NAME, "name says handout")
        if _CHEAT.search(name) or in_folders(_F_CHEAT):
            return _Verdict(DocType.CHEATSHEET, CONF_PATTERN, "cheat sheet")
        if in_folders(_F_TEXTBOOK):
            return _Verdict(DocType.TEXTBOOK, CONF_FOLDER, "textbook folder")

        exams = self._exam_keywords(name)
        has_exam = bool(exams) or self._exam_from_folders(folders) is not None
        solution_in_name = self._solution_in_name(name, folders, has_exam)
        in_lab = in_folders(_F_LAB)
        if (in_folders(_F_SOLUTION) and not in_lab) or (
            solution_in_name and (has_exam or not self._coursework(name))
        ):
            return _Verdict(DocType.SOLUTION, CONF_PATTERN, "solution keyword")
        if exams:
            return _Verdict(DocType.PYQ, CONF_NAME, "exam keyword in name")
        if has_exam:
            return _Verdict(DocType.PYQ, CONF_FOLDER, "exam folder")

        if _ASSIGNMENT.search(name) or in_folders(_F_ASSIGNMENT):
            return _Verdict(DocType.ASSIGNMENT, CONF_FOLDER, "homework / assignment")
        if _TUTORIAL.search(name) or in_folders(_F_TUTORIAL):
            return _Verdict(DocType.TUTORIAL, CONF_FOLDER, "tutorial / problem sheet")
        if solution_in_name:
            return _Verdict(DocType.SOLUTION, CONF_FOLDER, "solution keyword")
        if in_lab:
            return _Verdict(DocType.LAB, CONF_FOLDER, "lab folder")
        if in_folders(_F_SLIDES):
            return _Verdict(DocType.SLIDES, CONF_FOLDER, "slides folder")
        if in_folders(_F_NOTES) or _NOTES_NAME.search(name):
            return _Verdict(DocType.NOTES, CONF_FOLDER, "notes / guide")
        if _SLIDES_NAME.search(name):
            return _Verdict(DocType.SLIDES, 0.75, "lecture-style file name")
        return _Verdict(DocType.UNKNOWN, 0.0, "no rule matched")

    @staticmethod
    def _coursework(name: str) -> bool:
        """Whether the name looks like tutorial or homework material."""
        return bool(_TUTORIAL.search(name) or _ASSIGNMENT.search(name))

    def _is_coursework(self, name: str, folders: tuple[str, ...]) -> bool:
        """Whether name or folders say this is tutorial or homework material."""
        return self._coursework(name) or any(
            _F_TUTORIAL.search(f) or _F_ASSIGNMENT.search(f) or _F_LAB.search(f) for f in folders
        )

    @staticmethod
    def _solution_in_name(name: str, folders: tuple[str, ...], has_exam: bool) -> bool:
        """Whether the name says the file holds solutions or an answer key.

        Strong words (``soln``, ``answer key``) always count. The plain word
        ``solution`` also counts, except in a slides or notes folder where no
        exam is involved: there it is far more likely a topic title.
        """
        if _WITH_SOLUTION.search(name):
            return False
        if _STRONG_SOLUTION.search(name) or (has_exam and _EXAM_KEY.search(name)):
            return True
        if not _WEAK_SOLUTION.search(name):
            return False
        topic_folder = any(_F_SLIDES.search(f) or _F_NOTES.search(f) for f in folders)
        return has_exam or not topic_folder

    @staticmethod
    def _scope(folders: tuple[str, ...]) -> SyllabusScope | None:
        """Read "Pre Midsem" / "Post Midsem" from the folder names."""
        for folder in folders:
            if _F_PRE_MID.search(folder):
                return SyllabusScope.PRE_MIDSEM
            if _F_POST_MID.search(folder):
                return SyllabusScope.POST_MIDSEM
        return None

    def _has_solution(self, name: str, folders: tuple[str, ...]) -> bool:
        """Whether the file includes worked solutions or an answer key."""
        has_exam = bool(self._exam_keywords(name)) or self._exam_from_folders(folders) is not None
        return bool(
            _WITH_SOLUTION.search(name)
            or self._solution_in_name(name, folders, has_exam)
            or any(_F_SOLUTION.search(f) or _F_WITH_SOLUTION.search(f) for f in folders)
        )

    # ----------------------------------------------------------------- exam type

    @staticmethod
    def _exam_keywords(name: str) -> set[ExamType]:
        """Collect every exam keyword the name contains."""
        found: set[ExamType] = set()
        if _LAB_COMPRE.search(name):
            found.add(ExamType.LAB_COMPRE)
        elif _COMPRE.search(name):
            found.add(ExamType.COMPRE)
        if _LAB_QUIZ.search(name):
            found.add(ExamType.LAB_QUIZ)
        else:
            if _QUIZ.search(name):
                found.add(ExamType.QUIZ)
            if _TEST.search(name):
                found.add(ExamType.TEST)
        if _MIDSEM.search(name):
            found.add(ExamType.MIDSEM)
        return found

    @staticmethod
    def _exam_from_folders(folders: tuple[str, ...]) -> ExamType | None:
        """Return the exam named by the deepest folder that names one.

        Folders are read from the deepest outwards. A slides or notes folder
        met on the way means the file is study material that merely sits
        inside an exam-named folder (``Quiz/Slides and Notes``), so no exam.
        """
        for folder in reversed(folders):
            for pattern, exam in _F_EXAMS:
                if pattern.search(folder):
                    return exam
            if _F_SLIDES.search(folder) or _F_NOTES.search(folder):
                return None
        return None

    def _exam_type(self, name: str, folders: tuple[str, ...]) -> tuple[ExamType, float, str]:
        """Decide the exam type from the name first, then the folders.

        A name that mentions two exams ("Midsem_Quiz") is settled by the
        folder when the folder names one of them; otherwise it is a low
        confidence guess that goes to the review queue.
        """
        found = self._exam_keywords(name)
        from_folder = self._exam_from_folders(folders)
        if len(found) == 1:
            return next(iter(found)), CONF_NAME, ""
        if len(found) > 1:
            if from_folder in found and from_folder is not None:
                return from_folder, CONF_FOLDER, "name mentions two exams; folder decides"
            for preferred in (
                ExamType.LAB_QUIZ,
                ExamType.QUIZ,
                ExamType.TEST,
                ExamType.MIDSEM,
                ExamType.COMPRE,
            ):
                if preferred in found:
                    return preferred, CONF_CONFLICT, "name mentions more than one exam"
        if from_folder is not None:
            return from_folder, CONF_FOLDER, ""
        return ExamType.UNKNOWN, 0.0, "no exam keyword in name or folders"

    @staticmethod
    def _exam_number(name: str, folders: tuple[str, ...]) -> int | None:
        """Read a quiz or test number such as the 2 in ``"quiz 2"``.

        The name is tried first ("Quiz 2", "T3", "LQ9", "Answer Key Q1"), then
        the exam folder ("Lab Test 1").
        """
        match = (
            _NUMBER_AFTER_QUIZ.search(name)
            or _NUMBER_T.search(name)
            or _NUMBER_LQ.search(name)
            or _NUMBER_Q.search(name)
        )
        if match:
            return int(match[1])
        for folder in reversed(folders):
            if any(pattern.search(folder) for pattern, _ in _F_EXAMS):
                from_folder = _NUMBER_LAST.search(folder.strip())
                return int(from_folder[1]) if from_folder else None
        return None

    # ---------------------------------------------------------------------- year

    @staticmethod
    def _academic_year(
        parsed: ParsedPath, stem: str, folders: tuple[str, ...]
    ) -> tuple[int | None, str]:
        """Decide the academic year, trusting the semester folder unless it is a past archive.

        A folder such as ``Past Material`` sits inside one year's folder but
        holds papers from earlier years, so its year says nothing about the
        file. There the file name must supply the year, or it stays unknown.
        """
        name_year, is_span = year_from_name(stem)
        in_archive = any(_F_PAST.search(folder) for folder in folders)
        if parsed.folder_year is None:
            if name_year is None:
                return None, "no academic year found"
            return name_year, "" if is_span else "year taken from a lone year in the file name"
        if in_archive:
            if name_year is None:
                return None, "inside a past-material folder; year unknown"
            return name_year, "" if is_span else "year taken from a lone year in the file name"
        consistent = name_year == parsed.folder_year or (
            not is_span and name_year == parsed.folder_year + 1
        )
        if name_year is not None and not consistent:
            return parsed.folder_year, "year in the file name differs from its folder"
        return parsed.folder_year, ""
