"""The chat service: one message in, one structured reply out.

Reply kinds:

* ``smalltalk`` - a greeting or thanks; a fixed sentence.
* ``unclear``   - nothing usable was found in the message.
* ``clarify``   - the course is missing; the student picks one.
* ``confirm``   - we have a form to show ("Does this look right?").

All wording is fixed templates. The service never invents file names, and it
never asks a language model to write the reply.
"""

import logging
from dataclasses import dataclass
from typing import Protocol

from unidex.chat.models import Interpretation, ParseResult
from unidex.chat.parser import RuleParser
from unidex.chat.planner import ChatPlanner
from unidex.search.catalog import Catalog

logger = logging.getLogger(__name__)

KIND_SMALLTALK = "smalltalk"
KIND_UNCLEAR = "unclear"
KIND_CLARIFY = "clarify"
KIND_CONFIRM = "confirm"

UNCLEAR_TEXT = (
    "I could not find a course, exam or kind of material in that. "
    "Try something like “OOP midsem papers and notes” or “DD compre papers, last 3 years”."
)
CLARIFY_TEXT = "Which course is this for? I currently have these in the drives:"
CONFIRM_TEXT = "Here is what I understood. Does this look right before I search the drives?"


class CourseResolver(Protocol):
    """Anything that can guess courses from a message (see ``LlmCourseResolver``)."""

    def resolve(self, message: str) -> tuple[str, ...]:
        """Return course codes for a message, or an empty tuple."""
        ...


@dataclass(frozen=True, slots=True)
class CourseOption:
    """A course the student can pick.

    Attributes:
        code: Course code.
        label: Text to show.
    """

    code: str
    label: str


@dataclass(frozen=True, slots=True)
class ChatReply:
    """What the chat answers.

    Attributes:
        kind: One of the ``KIND_*`` values.
        text: The sentence to show.
        interpretation: The form so far (send it back with the next message).
        notes: Remarks to show under the form.
        course_options: Courses to pick from (for ``clarify`` and the course selector).
        estimated_files: How many files a search would show (for ``confirm``).
        used_llm: True when a language model helped.
    """

    kind: str
    text: str
    interpretation: Interpretation
    notes: tuple[str, ...] = ()
    course_options: tuple[CourseOption, ...] = ()
    estimated_files: int = 0
    used_llm: bool = False


class ChatService:
    """Reads messages and decides what to say back."""

    def __init__(
        self,
        catalog: Catalog,
        resolver: CourseResolver | None = None,
    ) -> None:
        """Create the service.

        Args:
            catalog: The searchable catalog (also the source of course names).
            resolver: Optional language-model helper for unknown course wording.
        """
        self._catalog = catalog
        self._parser = RuleParser(catalog.scan_courses)
        self._planner = ChatPlanner(catalog)
        self._resolver = resolver

    @property
    def planner(self) -> ChatPlanner:
        """The planner that answers confirmed forms."""
        return self._planner

    def course_options(self) -> tuple[CourseOption, ...]:
        """Return every course that has documents."""
        return tuple(
            CourseOption(code, self._catalog.course_label(code))
            for code in self._catalog.course_codes()
        )

    def reply(self, message: str, previous: Interpretation | None = None) -> ChatReply:
        """Answer one message.

        Args:
            message: What the student typed.
            previous: The form from earlier in this conversation, if any.

        Returns:
            The structured reply.
        """
        parsed = self._parser.parse(message, previous)
        if parsed.smalltalk:
            return ChatReply(KIND_SMALLTALK, parsed.smalltalk, parsed.interpretation)
        if not parsed.understood:
            return ChatReply(
                KIND_UNCLEAR,
                UNCLEAR_TEXT,
                parsed.interpretation,
                course_options=self.course_options(),
            )
        parsed = self._ask_model_for_course(message, parsed)
        interpretation = parsed.interpretation
        if not interpretation.courses:
            return ChatReply(
                KIND_CLARIFY,
                CLARIFY_TEXT,
                interpretation,
                notes=parsed.notes,
                course_options=self.course_options(),
            )
        plan = self._planner.run(interpretation)
        return ChatReply(
            KIND_CONFIRM,
            CONFIRM_TEXT,
            interpretation,
            notes=parsed.notes,
            course_options=self.course_options(),
            estimated_files=plan.total_files,
            used_llm=parsed.used_llm,
        )

    def _ask_model_for_course(self, message: str, parsed: ParseResult) -> ParseResult:
        """If the rules found no course, let the model pick one from the indexed courses."""
        if parsed.interpretation.courses or self._resolver is None:
            return parsed
        codes = self._resolver.resolve(message)
        if not codes:
            return parsed
        logger.info("Language model picked course(s) %s", ", ".join(codes))
        return ParseResult(
            interpretation=parsed.interpretation.with_changes(courses=codes),
            understood=True,
            used_llm=True,
            notes=parsed.notes,
        )
