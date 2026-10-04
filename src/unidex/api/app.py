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

from fastapi import FastAPI, Query, Request, Response
from fastapi.staticfiles import StaticFiles
from pydantic import Field

from unidex.api.presenter import to_facet_options, to_stack
from unidex.api.schemas import DetectedCourse, HealthResponse, SearchResponse, SuggestResponse
from unidex.config import Settings, load_settings
from unidex.db.connection import connect
from unidex.db.repositories import iter_document_views, load_alias_map
from unidex.models.enums import DocType, ExamType
from unidex.search.catalog import FACETS, Catalog, SearchFilters
from unidex.search.grouping import group_into_stacks

logger = logging.getLogger(__name__)

DEFAULT_WEB_DIR = Path(__file__).resolve().parents[3] / "web"
MAX_QUERY_LENGTH = 200
MAX_STACK_PAGE = 50
DEFAULT_STACK_PAGE = 10
SUGGESTION_PREFIX_LENGTH = 40

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
    finally:
        conn.close()
    catalog = Catalog(views, course_aliases=aliases)
    logger.info("Catalog ready: %d documents", len(catalog))
    return catalog


def create_app(
    catalog: Catalog | None = None,
    settings: Settings | None = None,
    web_dir: Path | None = DEFAULT_WEB_DIR,
) -> FastAPI:
    """Build the application.

    Args:
        catalog: A ready catalog (tests pass one in). When ``None`` the catalog
            is built from the database when the server starts.
        settings: Settings used to find the database; loaded from ``.env`` if omitted.
        web_dir: Folder with the static front end; ``None`` serves the API only.

    Returns:
        The configured FastAPI application.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.catalog = (
            catalog if catalog is not None else load_catalog(settings or load_settings())
        )
        yield

    app = FastAPI(title="Unidex", version="0.4.0", lifespan=lifespan)

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

    @app.get("/api/suggest", response_model=SuggestResponse)
    def suggest(
        request: Request,
        prefix: Annotated[str, Query(max_length=SUGGESTION_PREFIX_LENGTH)] = "",
    ) -> SuggestResponse:
        """Complete the word the student is typing."""
        return SuggestResponse(suggestions=current_catalog(request).suggest(prefix))

    @app.get("/api/facets")
    def facets(request: Request) -> dict[str, list[dict[str, str | int]]]:
        """List every filter option with counts over the whole catalog."""
        catalog_now = current_catalog(request)
        return {
            facet: [o.model_dump() for o in to_facet_options(catalog_now.facet_options(facet))]
            for facet in FACETS
        }

    if web_dir is not None and web_dir.is_dir():
        app.mount("/", StaticFiles(directory=web_dir, html=True), name="web")
    return app
