"""The FastAPI application.

Endpoints (all under ``/api``):

* ``GET /api/health``  - is the server up, and how many documents it knows.
* ``GET /api/search``  - text and/or filters, answered as stacks of files.
* ``GET /api/suggest`` - autocomplete for the search box.
* ``GET /api/facets``  - every filter option with counts over the whole catalog.

The catalog (BM25 index, trie, filter index) is built once when the server
starts and then only read, so requests are fast and need no database access.
The static front end in ``web/`` is served from ``/``.
"""

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Query, Request, Response
from fastapi.staticfiles import StaticFiles
from pydantic import Field
from starlette.middleware.sessions import SessionMiddleware

from unidex.api.auth_routes import CurrentUser, require_user
from unidex.api.auth_routes import router as auth_router
from unidex.api.chat_routes import router as chat_router
from unidex.api.presenter import to_facet_options, to_stack
from unidex.api.report_routes import router as report_router
from unidex.api.schemas import DetectedCourse, HealthResponse, SearchResponse, SuggestResponse
from unidex.api.sink import build_sink
from unidex.api.usage import RateLimiter, UsageLog
from unidex.auth.google import GoogleOAuth
from unidex.chat.llm_resolver import LlmCourseResolver
from unidex.chat.service import ChatService, CourseResolver
from unidex.config import Settings, load_settings
from unidex.content.quality import MIN_QUALITY
from unidex.db.connection import connect
from unidex.db.repositories import (
    LlmUsageRepository,
    iter_document_views,
    load_alias_map,
    load_searchable_texts,
)
from unidex.exceptions import LLMRateLimitError
from unidex.extraction.budget import LlmBudget
from unidex.extraction.llm_client import GeminiClient
from unidex.models.enums import DocType, ExamType
from unidex.search.catalog import FACETS, Catalog, SearchFilters
from unidex.search.grouping import group_into_stacks

logger = logging.getLogger(__name__)

DEFAULT_WEB_DIR = Path(__file__).resolve().parents[3] / "web"
MAX_QUERY_LENGTH = 200
MAX_STACK_PAGE = 50
DEFAULT_STACK_PAGE = 10
SUGGESTION_PREFIX_LENGTH = 40
SESSION_COOKIE = "unidex_session"
SESSION_DAYS = 7
REPORTS_PER_HOUR = 10

# Fonts come from Google Fonts; everything else must be our own files.
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "style-src 'self' https://fonts.googleapis.com; "
    "font-src https://fonts.gstatic.com; "
    "img-src 'self' data:; "
    "script-src 'self'; "
    "connect-src 'self'; "
    "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)
_DOCS_PREFIXES = ("/docs", "/redoc", "/openapi.json")


def load_catalog(settings: Settings) -> Catalog:
    """Read every document from the database into a catalog.

    Args:
        settings: Application settings (for the database path).

    Returns:
        The ready-to-search catalog.
    """
    conn = connect(settings.db_path)
    try:
        views = list(iter_document_views(conn))
        aliases = load_alias_map(conn)
        texts = load_searchable_texts(conn, MIN_QUALITY)
    finally:
        conn.close()
    catalog = Catalog(views, course_aliases=aliases, texts=texts)
    logger.info("Catalog ready: %d documents, %d with readable text", len(catalog), len(texts))
    return catalog


def build_course_resolver(settings: Settings, catalog: Catalog) -> CourseResolver | None:
    """Create the optional language-model course helper.

    Only used when ``GEMINI_API_KEY`` and ``GEMINI_MODEL`` are both set. Each use
    counts against the shared daily request budget.

    Args:
        settings: Application settings.
        catalog: The catalog (source of the course list).

    Returns:
        A resolver, or ``None`` when no model is configured.
    """
    if not (settings.gemini_api_key and settings.gemini_model):
        return None
    client = GeminiClient(settings.gemini_api_key, settings.gemini_model)

    def acquire() -> None:
        conn = connect(settings.db_path)
        try:
            budget = LlmBudget(
                LlmUsageRepository(conn),
                per_minute=settings.llm_requests_per_minute,
                per_day=settings.llm_requests_per_day,
                sleep=_refuse_to_wait,
            )
            budget.acquire()
        finally:
            conn.close()

    courses = [
        (code, catalog.course_label(code).split(" · ", 1)[-1]) for code in catalog.course_codes()
    ]
    return LlmCourseResolver(client, courses, acquire)


def _refuse_to_wait(seconds: float) -> None:
    """Chat must answer quickly, so a full minute window means 'skip the model'."""
    raise LLMRateLimitError(f"Per-minute limit reached; not waiting {seconds:.0f}s in chat")


def create_app(
    catalog: Catalog | None = None,
    settings: Settings | None = None,
    web_dir: Path | None = DEFAULT_WEB_DIR,
    course_resolver: CourseResolver | None = None,
    oauth: GoogleOAuth | None = None,
) -> FastAPI:
    """Build the application.

    Args:
        catalog: A ready catalog (tests pass one in). When ``None`` the catalog
            is built from the database when the server starts.
        settings: Settings used to find the database; loaded from ``.env`` if omitted.
        web_dir: Folder with the static front end; ``None`` serves the API only.
        course_resolver: Optional language-model helper for the chat (tests pass a fake;
            when ``None`` and the catalog is loaded from the database, one is created
            if a Gemini key and model are configured).
        oauth: Google sign-in helper (tests pass a fake). When ``None`` one is created if
            the settings contain the Google client id and secret and a session secret.
            With no helper the site is open to everyone (normal for local development).

    Returns:
        The configured FastAPI application.
    """
    active = settings if settings is not None else (load_settings() if catalog is None else None)
    active_settings = active or Settings.from_mapping({})
    if oauth is None and active_settings.login_configured:
        oauth = GoogleOAuth(
            active_settings.google_client_id or "", active_settings.google_client_secret or ""
        )
    if oauth is None:
        logger.warning("Google login is not configured: the site is open to everyone")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        resolver = course_resolver
        if catalog is not None:
            app.state.catalog = catalog
        else:
            app.state.catalog = load_catalog(active_settings)
            if resolver is None:
                resolver = build_course_resolver(active_settings, app.state.catalog)
        app.state.chat = ChatService(app.state.catalog, resolver)
        yield
        sink.close()

    sink = build_sink(active_settings.event_webhook_url, active_settings.event_webhook_secret)
    # The interactive API pages would show the API to anyone, so they are only for open
    # (local development) servers.
    public_docs = oauth is None
    app = FastAPI(
        title="Unidex",
        version="0.7.0",
        lifespan=lifespan,
        docs_url="/docs" if public_docs else None,
        redoc_url="/redoc" if public_docs else None,
        openapi_url="/openapi.json" if public_docs else None,
    )
    app.state.settings = active_settings
    app.state.oauth = oauth
    app.state.usage = UsageLog(active.db_path if active is not None else None, sink=sink)
    app.state.report_limiter = RateLimiter(REPORTS_PER_HOUR, 3600)
    if oauth is not None:
        app.add_middleware(
            SessionMiddleware,
            secret_key=active_settings.session_secret or "",
            session_cookie=SESSION_COOKIE,
            max_age=SESSION_DAYS * 24 * 3600,
            same_site="lax",
            https_only=(active_settings.public_url or "").startswith("https://"),
        )

    @app.middleware("http")
    async def security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if not request.url.path.startswith(_DOCS_PREFIXES):
            response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
        return response

    def current_catalog(request: Request) -> Catalog:
        found: Catalog = request.app.state.catalog
        return found

    @app.get("/api/health", response_model=HealthResponse)
    def health(request: Request) -> HealthResponse:
        """Report that the server is running."""
        return HealthResponse(status="ok", documents=len(current_catalog(request)))

    @app.get("/api/search", response_model=SearchResponse)
    def search(
        request: Request,
        user: CurrentUser,
        q: Annotated[str, Query(max_length=MAX_QUERY_LENGTH)] = "",
        course: Annotated[list[str] | None, Query(max_length=20)] = None,
        doc_type: Annotated[list[DocType] | None, Query(max_length=20)] = None,
        exam_type: Annotated[list[ExamType] | None, Query(max_length=20)] = None,
        year: Annotated[
            list[Annotated[int, Field(ge=2000, le=2100)]] | None, Query(max_length=20)
        ] = None,
        detect: bool = True,
        stack_offset: Annotated[int, Query(ge=0)] = 0,
        stack_limit: Annotated[int, Query(ge=1, le=MAX_STACK_PAGE)] = DEFAULT_STACK_PAGE,
    ) -> SearchResponse:
        """Search by text, filters, or both; answers are grouped into stacks.

        A course nickname in ``q`` (such as "oop") becomes a course filter unless
        ``course`` is given or ``detect=false``.
        """
        filters = SearchFilters(
            courses=frozenset(c.strip() for c in course or [] if c.strip()),
            doc_types=frozenset(d.value for d in doc_type or []),
            exam_types=frozenset(e.value for e in exam_type or []),
            years=frozenset(year or []),
        )
        catalog_now = current_catalog(request)
        result = catalog_now.search(q, filters, detect_courses=detect)
        request.app.state.usage.search(
            user.label,
            q,
            {
                "courses": sorted(filters.courses),
                "doc_types": sorted(filters.doc_types),
                "exam_types": sorted(filters.exam_types),
                "years": sorted(filters.years),
            },
            result.total,
            source="search",
        )
        stacks = group_into_stacks(result.hits)
        page = stacks[stack_offset : stack_offset + stack_limit]
        return SearchResponse(
            query=q,
            total_files=result.total,
            total_stacks=len(stacks),
            truncated=result.total > len(result.hits),
            stack_offset=stack_offset,
            has_more=stack_offset + stack_limit < len(stacks),
            stacks=[to_stack(stack) for stack in page],
            facets={facet: to_facet_options(result.facets[facet]) for facet in FACETS},
            detected_courses=[
                DetectedCourse(code=code, label=catalog_now.course_label(code))
                for code in result.detected_courses
            ],
        )

    @app.get("/api/suggest", response_model=SuggestResponse, dependencies=[Depends(require_user)])
    def suggest(
        request: Request,
        prefix: Annotated[str, Query(max_length=SUGGESTION_PREFIX_LENGTH)] = "",
    ) -> SuggestResponse:
        """Complete the word the student is typing."""
        return SuggestResponse(suggestions=current_catalog(request).suggest(prefix))

    @app.get("/api/facets", dependencies=[Depends(require_user)])
    def facets(request: Request) -> dict[str, list[dict[str, str | int]]]:
        """List every filter option with counts over the whole catalog."""
        catalog_now = current_catalog(request)
        return {
            facet: [o.model_dump() for o in to_facet_options(catalog_now.facet_options(facet))]
            for facet in FACETS
        }

    app.include_router(auth_router)
    app.include_router(chat_router, dependencies=[Depends(require_user)])
    app.include_router(report_router)

    if web_dir is not None and web_dir.is_dir():
        app.mount("/", StaticFiles(directory=web_dir, html=True), name="web")
    return app
