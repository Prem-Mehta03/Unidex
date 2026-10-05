"""JSON shapes for the chat endpoints."""

from typing import Annotated

from pydantic import BaseModel, Field

from unidex.api.schemas import StackOut
from unidex.chat.models import (
    MAX_RECENT_YEARS,
    MAX_TOPIC_LENGTH,
    MAX_TOPICS,
    Interpretation,
    Material,
)
from unidex.models.enums import ExamType

MAX_COURSES = 5
MAX_MESSAGE_CHARS = 500
_EXAMS_ALLOWED = (
    ExamType.QUIZ,
    ExamType.TEST,
    ExamType.MIDSEM,
    ExamType.COMPRE,
    ExamType.LAB_COMPRE,
    ExamType.LAB_QUIZ,
)

CourseCode = Annotated[str, Field(min_length=1, max_length=40)]
Topic = Annotated[str, Field(min_length=1, max_length=MAX_TOPIC_LENGTH)]
Year = Annotated[int, Field(ge=2000, le=2100)]


class InterpretationModel(BaseModel):
    """The form shown on the confirm card (also sent back by the browser).

    Attributes:
        courses: Course codes.
        exam_types: Exam types for papers.
        materials: Kinds of material wanted.
        years: Academic start years asked for explicitly.
        recent_years: "last N years", if asked.
        topics: Topic phrases.
        quiz_number: Quiz or test number, if one was named.
    """

    courses: list[CourseCode] = Field(default_factory=list, max_length=MAX_COURSES)
    exam_types: list[ExamType] = Field(default_factory=list, max_length=len(_EXAMS_ALLOWED))
    materials: list[Material] = Field(default_factory=list, max_length=len(Material))
    years: list[Year] = Field(default_factory=list, max_length=20)
    recent_years: Annotated[int, Field(ge=1, le=MAX_RECENT_YEARS)] | None = None
    topics: list[Topic] = Field(default_factory=list, max_length=MAX_TOPICS)
    quiz_number: Annotated[int, Field(ge=1, le=20)] | None = None

    def to_domain(self) -> Interpretation:
        """Convert to the plain data class used by the chat layer."""
        allowed = set(_EXAMS_ALLOWED)
        return Interpretation(
            courses=tuple(dict.fromkeys(c.strip() for c in self.courses if c.strip())),
            exam_types=tuple(dict.fromkeys(e.value for e in self.exam_types if e in allowed)),
            materials=tuple(dict.fromkeys(self.materials)),
            years=tuple(dict.fromkeys(self.years)),
            recent_years=self.recent_years,
            topics=tuple(dict.fromkeys(t.strip() for t in self.topics if t.strip())),
            quiz_number=self.quiz_number,
        )

    @classmethod
    def from_domain(cls, value: Interpretation) -> "InterpretationModel":
        """Convert from the plain data class.

        Args:
            value: The interpretation.

        Returns:
            The JSON model.
        """
        return cls(
            courses=list(value.courses),
            exam_types=[ExamType(e) for e in value.exam_types],
            materials=list(value.materials),
            years=list(value.years),
            recent_years=value.recent_years,
            topics=list(value.topics),
            quiz_number=value.quiz_number,
        )


class MessageRequest(BaseModel):
    """A chat message.

    Attributes:
        message: What the student typed.
        previous: The form from earlier in the conversation, if any.
    """

    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)
    previous: InterpretationModel | None = None


class ResultsRequest(BaseModel):
    """A confirmed form to search for.

    Attributes:
        interpretation: The (possibly edited) form.
        final: True when the student pressed "Search drives"; false for the live estimate
            shown while editing. Only final searches are written to the search log.
    """

    interpretation: InterpretationModel
    final: bool = False


class CourseOptionOut(BaseModel):
    """A course the student can pick.

    Attributes:
        code: Course code.
        label: Text to show.
    """

    code: str
    label: str


class ChoiceOut(BaseModel):
    """A fixed choice (a kind of material or an exam).

    Attributes:
        value: Value sent back to the server.
        label: Text to show.
    """

    value: str
    label: str


class ChatReplyOut(BaseModel):
    """The chat's answer to one message.

    Attributes:
        kind: ``smalltalk``, ``unclear``, ``clarify`` or ``confirm``.
        text: The sentence to show.
        interpretation: The form so far.
        notes: Remarks to show under the form.
        course_options: Courses to pick from.
        estimated_files: How many files a search would show.
        used_llm: Whether a language model helped.
    """

    kind: str
    text: str
    interpretation: InterpretationModel
    notes: list[str]
    course_options: list[CourseOptionOut]
    estimated_files: int
    used_llm: bool


class ChatResultsOut(BaseModel):
    """Files found for a confirmed form.

    Attributes:
        total_files: Number of files.
        total_stacks: Number of stacks.
        notes: Remarks about how the search was done.
        stacks: The stacks (all of them; the chat shows a handful at a time).
    """

    total_files: int
    total_stacks: int
    notes: list[str]
    stacks: list[StackOut]


class ChatOptionsOut(BaseModel):
    """Everything the confirm card needs to draw its controls.

    Attributes:
        courses: Indexed courses.
        materials: Kinds of material.
        exams: Exam types.
    """

    courses: list[CourseOptionOut]
    materials: list[ChoiceOut]
    exams: list[ChoiceOut]
