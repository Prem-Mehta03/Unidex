"""Report route: ``POST /api/report`` ("Wrong info?" and "Broken link?" buttons)."""

import logging
import sqlite3

from fastapi import APIRouter, HTTPException, Request

from unidex.api.auth_routes import CurrentUser
from unidex.api.schemas import ReportRequest, ReportResponse
from unidex.api.usage import RateLimiter, UsageLog

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["reports"])

RECORDED = "recorded"
ALREADY = "already_reported"


@router.post("/report", response_model=ReportResponse)
def report(request: Request, body: ReportRequest, user: CurrentUser) -> ReportResponse:
    """Record that a file's tags are wrong or its link is broken."""
    usage: UsageLog = request.app.state.usage
    limiter: RateLimiter = request.app.state.report_limiter
    if not usage.enabled:
        raise HTTPException(status_code=503, detail="Reports are not available right now.")
    if not limiter.allow(user.label):
        raise HTTPException(
            status_code=429, detail="You have sent several reports in a short time. Try later."
        )
    try:
        outcome = usage.report(body.file_id, body.type.value, body.note or "", user.label)
    except sqlite3.Error as exc:
        logger.exception("Could not store a report")
        raise HTTPException(status_code=503, detail="Could not save the report.") from exc
    if outcome is None:
        raise HTTPException(status_code=404, detail="That file is not in the index.")
    report_id, is_new = outcome
    logger.info("Report %d (%s) on file %s", report_id, body.type.value, body.file_id)
    return ReportResponse(status=RECORDED if is_new else ALREADY)
