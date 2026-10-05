"""Chat endpoints: ``/api/chat/options``, ``/api/chat/message`` and ``/api/chat/results``."""

from fastapi import APIRouter, Request

from unidex.api.auth_routes import CurrentUser
from unidex.api.chat_schemas import (
    ChatOptionsOut,
    ChatReplyOut,
    ChatResultsOut,
    ChoiceOut,
    CourseOptionOut,
    InterpretationModel,
    MessageRequest,
    ResultsRequest,
)
from unidex.api.presenter import to_stack
from unidex.api.usage import UsageLog
from unidex.chat.models import MATERIAL_LABELS
from unidex.chat.service import ChatService
from unidex.search.labels import EXAM_TYPE_LABELS

router = APIRouter(prefix="/api/chat", tags=["chat"])


def _service(request: Request) -> ChatService:
    """Return the chat service created at start-up."""
    service: ChatService = request.app.state.chat
    return service


def _course_options(service: ChatService) -> list[CourseOptionOut]:
    return [CourseOptionOut(code=o.code, label=o.label) for o in service.course_options()]


@router.get("/options", response_model=ChatOptionsOut)
def options(request: Request) -> ChatOptionsOut:
    """List the choices the confirm card offers."""
    return ChatOptionsOut(
        courses=_course_options(_service(request)),
        materials=[ChoiceOut(value=m.value, label=label) for m, label in MATERIAL_LABELS.items()],
        exams=[ChoiceOut(value=value, label=label) for value, label in EXAM_TYPE_LABELS.items()],
    )


@router.post("/message", response_model=ChatReplyOut)
def message(request: Request, body: MessageRequest) -> ChatReplyOut:
    """Read a message and answer with a form to confirm, a question, or a short reply."""
    service = _service(request)
    previous = body.previous.to_domain() if body.previous else None
    reply = service.reply(body.message, previous)
    return ChatReplyOut(
        kind=reply.kind,
        text=reply.text,
        interpretation=InterpretationModel.from_domain(reply.interpretation),
        notes=list(reply.notes),
        course_options=[CourseOptionOut(code=o.code, label=o.label) for o in reply.course_options],
        estimated_files=reply.estimated_files,
        used_llm=reply.used_llm,
    )


@router.post("/results", response_model=ChatResultsOut)
def results(request: Request, body: ResultsRequest, user: CurrentUser) -> ChatResultsOut:
    """Search the drives for a confirmed form."""
    form = body.interpretation.to_domain()
    plan = _service(request).planner.run(form)
    if body.final:
        usage: UsageLog = request.app.state.usage
        usage.search(
            user.label,
            "; ".join(form.topics),
            {
                "courses": list(form.courses),
                "exam_types": list(form.exam_types),
                "materials": [m.value for m in form.materials],
                "years": list(form.years),
                "recent_years": form.recent_years,
            },
            plan.total_files,
            source="chat",
        )
    return ChatResultsOut(
        total_files=plan.total_files,
        total_stacks=len(plan.stacks),
        notes=list(plan.notes),
        stacks=[to_stack(stack) for stack in plan.stacks],
    )
