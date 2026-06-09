"""FastAPI routes: health, status, targets, reviews, scan and manual review."""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Request

from app.db import list_recent_reviews, session_scope

logger = logging.getLogger(__name__)

router = APIRouter()


def _service(request: Request):
    service = getattr(request.app.state, "pr_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Service not initialised")
    return service


@router.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@router.get("/status")
def status(request: Request) -> Dict[str, Any]:
    settings = request.app.state.settings
    scheduler = getattr(request.app.state, "scheduler", None)
    policy = settings.app.review_policy
    return {
        "scheduler_running": bool(scheduler and scheduler.running),
        "interval_seconds": settings.app.scheduler.interval_seconds,
        "dry_run": policy.dry_run,
        "auto_comment": policy.auto_comment,
        "auto_approve": policy.auto_approve,
        "auto_complete": policy.auto_complete,
        "enabled_targets": len(settings.app.enabled_targets()),
        "llm_model": settings.app.llm.model,
    }


@router.get("/targets")
def targets(request: Request) -> List[Dict[str, Any]]:
    settings = request.app.state.settings
    return [t.model_dump() for t in settings.app.targets]


@router.get("/reviews")
def reviews(request: Request, limit: int = 50) -> List[Dict[str, Any]]:
    with session_scope() as session:
        rows = list_recent_reviews(session, limit=limit)
        return [
            {
                "id": r.id,
                "project": r.project,
                "repository": r.repository,
                "pull_request_id": r.pull_request_id,
                "target_branch": r.target_branch,
                "latest_commit_id": r.latest_commit_id,
                "reviewed_at": r.reviewed_at.isoformat() if r.reviewed_at else None,
                "decision": r.decision,
                "confidence": r.confidence,
                "risk_level": r.risk_level,
                "comment_posted": r.comment_posted,
                "approved": r.approved,
                "completed": r.completed,
                "error_message": r.error_message,
            }
            for r in rows
        ]


@router.post("/scan")
def scan(request: Request) -> Dict[str, Any]:
    """Trigger a full scan of all enabled targets synchronously."""
    service = _service(request)
    outcomes = service.scan_all()
    return {"processed": len(outcomes), "results": [o.to_dict() for o in outcomes]}


@router.post("/review/{project}/{repository}/{pull_request_id}")
def review_one(
    request: Request, project: str, repository: str, pull_request_id: int
) -> Dict[str, Any]:
    """Manually review a single PR, honouring the configured dry_run flag."""
    service = _service(request)
    outcome = service.review_pull_request(
        project, repository, pull_request_id, dry_run=None, force=True
    )
    return outcome.to_dict()


@router.post("/dry-run/{project}/{repository}/{pull_request_id}")
def dry_run_one(
    request: Request, project: str, repository: str, pull_request_id: int
) -> Dict[str, Any]:
    """Review a single PR in forced dry-run mode (no side effects)."""
    service = _service(request)
    outcome = service.review_pull_request(
        project, repository, pull_request_id, dry_run=True, force=True
    )
    return outcome.to_dict()
