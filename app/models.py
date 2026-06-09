"""SQLAlchemy ORM models for persisted review state."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class ReviewedPullRequest(Base):
    """One row per (project, repo, PR, commit) reviewed.

    The unique constraint on ``(project, repository, pull_request_id,
    latest_commit_id)`` lets us skip re-reviewing the same commit while still
    re-reviewing when new commits land.
    """

    __tablename__ = "reviewed_pull_requests"
    __table_args__ = (
        UniqueConstraint(
            "project",
            "repository",
            "pull_request_id",
            "latest_commit_id",
            name="uq_reviewed_pr_commit",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project: Mapped[str] = mapped_column(String(200), index=True)
    repository: Mapped[str] = mapped_column(String(200), index=True)
    pull_request_id: Mapped[int] = mapped_column(Integer, index=True)
    source_branch: Mapped[str] = mapped_column(String(400), default="")
    target_branch: Mapped[str] = mapped_column(String(400), default="")
    latest_commit_id: Mapped[str] = mapped_column(String(80), default="")
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    decision: Mapped[str] = mapped_column(String(40), default="")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    risk_level: Mapped[str] = mapped_column(String(20), default="")
    comment_posted: Mapped[bool] = mapped_column(Boolean, default=False)
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    error_message: Mapped[str] = mapped_column(Text, default="")


class ReviewAction(Base):
    """Audit log of individual actions taken (comment/approve/complete/etc.)."""

    __tablename__ = "review_actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project: Mapped[str] = mapped_column(String(200), index=True)
    repository: Mapped[str] = mapped_column(String(200), index=True)
    pull_request_id: Mapped[int] = mapped_column(Integer, index=True)
    action_type: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20))  # success | failure | skipped | dry_run
    message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
