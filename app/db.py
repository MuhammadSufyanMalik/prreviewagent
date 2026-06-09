"""SQLite database engine, session management and small data-access helpers."""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Iterator, List, Optional

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.models import Base, ReviewAction, ReviewedPullRequest

logger = logging.getLogger(__name__)

_engine = None
_SessionLocal: Optional[sessionmaker] = None


def init_db(database_path: str) -> None:
    """Create the engine, ensure the parent directory and tables exist."""
    global _engine, _SessionLocal

    parent = os.path.dirname(os.path.abspath(database_path))
    os.makedirs(parent, exist_ok=True)

    _engine = create_engine(
        f"sqlite:///{database_path}",
        future=True,
        connect_args={"check_same_thread": False},
    )
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    Base.metadata.create_all(_engine)
    logger.info("Database initialised at %s", database_path)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional session scope."""
    if _SessionLocal is None:
        raise RuntimeError("Database not initialised. Call init_db() first.")
    session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_reviewed_for_commit(
    session: Session,
    project: str,
    repository: str,
    pull_request_id: int,
    commit_id: str,
) -> Optional[ReviewedPullRequest]:
    """Return the review row for a specific commit, if one exists."""
    stmt = select(ReviewedPullRequest).where(
        ReviewedPullRequest.project == project,
        ReviewedPullRequest.repository == repository,
        ReviewedPullRequest.pull_request_id == pull_request_id,
        ReviewedPullRequest.latest_commit_id == commit_id,
    )
    return session.scalars(stmt).first()


def record_action(
    session: Session,
    *,
    project: str,
    repository: str,
    pull_request_id: int,
    action_type: str,
    status: str,
    message: str = "",
) -> None:
    """Append an entry to the action audit log."""
    session.add(
        ReviewAction(
            project=project,
            repository=repository,
            pull_request_id=pull_request_id,
            action_type=action_type,
            status=status,
            message=message,
        )
    )


def list_recent_reviews(session: Session, limit: int = 50) -> List[ReviewedPullRequest]:
    stmt = (
        select(ReviewedPullRequest)
        .order_by(ReviewedPullRequest.reviewed_at.desc())
        .limit(limit)
    )
    return list(session.scalars(stmt).all())
