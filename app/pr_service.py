"""Orchestration: scan, review, comment, approve and complete pull requests.

This module wires the Azure DevOps client, the LLM review engine, the
deterministic decision policy and the SQLite store together, enforcing the
ordering guarantees the agent promises:

  comment -> approve -> complete

and never advancing a step if the previous one failed or a safety check could
not be verified.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

from app.azure_devops_client import AzureDevOpsClient, AzureDevOpsError
from app.config import Settings, Target
from app.db import get_reviewed_for_commit, record_action, session_scope
from app.decision_policy import (
    VOTE_APPROVE,
    PRContext,
    PolicyDecision,
    evaluate,
)
from app.models import ReviewedPullRequest
from app.review_engine import ReviewEngine, ReviewResult

logger = logging.getLogger(__name__)


@dataclass
class PRReviewOutcome:
    project: str
    repository: str
    pull_request_id: int
    status: str  # reviewed | skipped | error | dry_run
    decision: str = ""
    confidence: float = 0.0
    risk_level: str = ""
    commented: bool = False
    approved: bool = False
    completed: bool = False
    message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class PRService:
    def __init__(
        self,
        settings: Settings,
        ado_client: AzureDevOpsClient,
        review_engine: ReviewEngine,
    ) -> None:
        self._settings = settings
        self._ado = ado_client
        self._engine = review_engine
        self._policy = settings.app.review_policy
        self._reviewer_id = settings.app.reviewer.reviewer_id

    # ------------------------------------------------------------------ #
    # Public entry points
    # ------------------------------------------------------------------ #
    def scan_all(self) -> List[PRReviewOutcome]:
        """Scan every enabled target and review eligible PRs."""
        outcomes: List[PRReviewOutcome] = []
        for target in self._settings.app.enabled_targets():
            try:
                outcomes.extend(self.scan_target(target))
            except Exception as exc:  # noqa: BLE001 - keep scanning other targets
                logger.exception(
                    "Failed scanning %s/%s: %s",
                    target.project,
                    target.repository,
                    exc,
                )
        return outcomes

    def scan_target(self, target: Target) -> List[PRReviewOutcome]:
        outcomes: List[PRReviewOutcome] = []
        branches = target.target_branches or [None]  # type: ignore[list-item]
        seen: set[int] = set()
        for branch in branches:
            prs = self._ado.get_active_pull_requests(
                target.project, target.repository, target_branch=branch
            )
            for pr in prs:
                pr_id = pr.get("pullRequestId")
                if pr_id is None or pr_id in seen:
                    continue
                seen.add(pr_id)
                outcomes.append(
                    self.review_pull_request(
                        target.project, target.repository, pr_id, dry_run=None
                    )
                )
        return outcomes

    def review_pull_request(
        self,
        project: str,
        repository: str,
        pull_request_id: int,
        *,
        dry_run: Optional[bool] = None,
        force: bool = False,
    ) -> PRReviewOutcome:
        """Review a single PR end to end.

        ``dry_run`` overrides the configured policy when not None.
        ``force`` re-reviews even if this commit was already reviewed.
        """
        effective_dry_run = self._policy.dry_run if dry_run is None else dry_run
        outcome = PRReviewOutcome(
            project=project,
            repository=repository,
            pull_request_id=pull_request_id,
            status="reviewed",
        )

        try:
            pr = self._ado.get_pull_request(project, repository, pull_request_id)
        except AzureDevOpsError as exc:
            logger.error("Could not fetch PR %s: %s", pull_request_id, exc)
            outcome.status = "error"
            outcome.message = str(exc)
            return outcome

        source_branch = pr.get("sourceRefName", "")
        target_branch = pr.get("targetRefName", "")
        title = pr.get("title", "")
        description = pr.get("description", "")
        merge_status = pr.get("mergeStatus")  # e.g. "succeeded", "conflicts"
        has_conflicts = merge_status == "conflicts" if merge_status else None

        commit_id = self._ado.get_latest_commit_id(
            project, repository, pull_request_id
        ) or pr.get("lastMergeSourceCommit", {}).get("commitId", "")

        # Skip if already reviewed for this commit.
        if not force:
            with session_scope() as session:
                existing = get_reviewed_for_commit(
                    session, project, repository, pull_request_id, commit_id
                )
                if existing is not None:
                    logger.info(
                        "PR %s commit %s already reviewed; skipping",
                        pull_request_id,
                        commit_id[:8],
                    )
                    outcome.status = "skipped"
                    outcome.message = "already reviewed for this commit"
                    return outcome

        # Gather change set.
        change_entries = self._ado.get_changed_files(
            project, repository, pull_request_id
        )
        changed_paths = [
            (c.get("item", {}) or {}).get("path", "")
            for c in change_entries
            if (c.get("item", {}) or {}).get("path")
        ]

        diff_text = self._collect_diff_text(
            project, repository, pull_request_id, pr, changed_paths
        )
        diff_lines = diff_text.count("\n") + 1 if diff_text else 0

        existing_comments = self._collect_existing_comments(
            project, repository, pull_request_id
        )

        # Build/policy status (best effort).
        build_ok, policy_ok = self._collect_status(
            project, repository, pull_request_id
        )

        ctx = PRContext(
            project=project,
            repository=repository,
            pull_request_id=pull_request_id,
            target_branch=target_branch,
            changed_files_count=len(changed_paths),
            diff_lines=diff_lines,
            changed_paths=changed_paths,
            build_succeeded=build_ok,
            policy_succeeded=policy_ok,
            has_conflicts=has_conflicts,
        )

        # --- Run the LLM review ----------------------------------------- #
        try:
            user_prompt = self._engine.build_user_prompt(
                title=title,
                description=description,
                source_branch=source_branch,
                target_branch=target_branch,
                changed_files=changed_paths,
                diff_text=diff_text,
                existing_comments=existing_comments,
            )
            review = self._engine.review(user_prompt)
        except Exception as exc:  # noqa: BLE001
            logger.error("LLM review failed for PR %s: %s", pull_request_id, exc)
            outcome.status = "error"
            outcome.message = f"review failed: {exc}"
            self._persist(outcome, ctx, commit_id, source_branch, review=None)
            return outcome

        outcome.decision = review.decision
        outcome.confidence = review.confidence
        outcome.risk_level = review.risk_level

        # --- Deterministic policy --------------------------------------- #
        policy_decision = evaluate(review, ctx, self._policy)
        logger.info(
            "PR %s: llm_decision=%s vote=%s comment=%s approve=%s complete=%s",
            pull_request_id,
            review.decision,
            policy_decision.vote,
            policy_decision.should_comment,
            policy_decision.should_approve,
            policy_decision.should_complete,
        )

        if effective_dry_run:
            outcome.status = "dry_run"
            outcome.message = self._dry_run_summary(review, policy_decision)
            self._log_action(
                project, repository, pull_request_id, "dry_run", "dry_run",
                outcome.message,
            )
            self._persist(outcome, ctx, commit_id, source_branch, review)
            return outcome

        # --- Execute: comment -> approve -> complete -------------------- #
        self._execute_actions(
            project, repository, pull_request_id, commit_id, review,
            policy_decision, outcome,
        )
        self._persist(outcome, ctx, commit_id, source_branch, review)
        return outcome

    # ------------------------------------------------------------------ #
    # Action execution
    # ------------------------------------------------------------------ #
    def _execute_actions(
        self,
        project: str,
        repository: str,
        pull_request_id: int,
        commit_id: str,
        review: ReviewResult,
        decision: PolicyDecision,
        outcome: PRReviewOutcome,
    ) -> None:
        # 1. Comment first, always, before any approval.
        if decision.should_comment:
            try:
                self._post_review_comments(
                    project, repository, pull_request_id, review
                )
                outcome.commented = True
                self._log_action(
                    project, repository, pull_request_id, "comment", "success", ""
                )
            except Exception as exc:  # noqa: BLE001
                outcome.status = "error"
                outcome.message = f"comment failed: {exc}"
                self._log_action(
                    project, repository, pull_request_id, "comment", "failure",
                    str(exc),
                )
                logger.error("Comment failed for PR %s: %s", pull_request_id, exc)
                return  # No approval/merge if comment failed.

        # 2. Approve only after a successful comment.
        if decision.should_approve:
            if not outcome.commented:
                logger.warning(
                    "Refusing to approve PR %s: comment was not posted",
                    pull_request_id,
                )
                return
            try:
                self._ado.set_reviewer_vote(
                    project, repository, pull_request_id,
                    self._reviewer_id, decision.vote,
                )
                outcome.approved = True
                self._log_action(
                    project, repository, pull_request_id, "approve", "success",
                    f"vote={decision.vote}",
                )
            except Exception as exc:  # noqa: BLE001
                outcome.status = "error"
                outcome.message = f"approval failed: {exc}"
                self._log_action(
                    project, repository, pull_request_id, "approve", "failure",
                    str(exc),
                )
                logger.error("Approval failed for PR %s: %s", pull_request_id, exc)
                return  # No merge if approval failed.

        # 3. Complete only after a successful approval.
        if decision.should_complete:
            if not (outcome.commented and outcome.approved):
                logger.warning(
                    "Refusing to complete PR %s: comment/approve preconditions unmet",
                    pull_request_id,
                )
                return
            commit_for_merge = (
                self._ado.get_pull_request(project, repository, pull_request_id)
                .get("lastMergeSourceCommit", {})
                .get("commitId", commit_id)
            )
            try:
                self.complete_pull_request(
                    project=project,
                    repository=repository,
                    pull_request_id=pull_request_id,
                    merge_strategy=self._policy.merge_strategy,
                    delete_source_branch=self._policy.delete_source_branch_after_merge,
                    last_merge_source_commit=commit_for_merge,
                    _preconditions_met=True,
                )
                outcome.completed = True
                self._log_action(
                    project, repository, pull_request_id, "complete", "success", ""
                )
            except Exception as exc:  # noqa: BLE001
                outcome.status = "error"
                outcome.message = f"complete failed: {exc}"
                self._log_action(
                    project, repository, pull_request_id, "complete", "failure",
                    str(exc),
                )
                logger.error("Complete failed for PR %s: %s", pull_request_id, exc)

    def complete_pull_request(
        self,
        project: str,
        repository: str,
        pull_request_id: int,
        merge_strategy: str,
        delete_source_branch: bool,
        last_merge_source_commit: Optional[str] = None,
        _preconditions_met: bool = False,
    ) -> Dict[str, Any]:
        """Complete/merge a PR, enforcing safety preconditions.

        Refuses if: dry-run is on, the PR has conflicts, build/policy required
        but not successful, or the precondition flag (set by the orchestration
        after comment+approve) is not present.
        """
        if self._policy.dry_run:
            raise RuntimeError("refusing to complete in dry-run mode")
        if not _preconditions_met:
            raise RuntimeError(
                "refusing to complete: comment+approve preconditions not verified"
            )

        pr = self._ado.get_pull_request(project, repository, pull_request_id)
        if pr.get("mergeStatus") == "conflicts":
            raise RuntimeError("refusing to complete: PR has merge conflicts")

        if self._policy.require_successful_build or self._policy.require_successful_policy:
            build_ok, policy_ok = self._collect_status(
                project, repository, pull_request_id
            )
            if self._policy.require_successful_build and build_ok is not True:
                raise RuntimeError("refusing to complete: build not successful/unknown")
            if self._policy.require_successful_policy and policy_ok is not True:
                raise RuntimeError("refusing to complete: policy not successful/unknown")

        commit = last_merge_source_commit or pr.get("lastMergeSourceCommit", {}).get(
            "commitId", ""
        )
        if not commit:
            raise RuntimeError("refusing to complete: no merge source commit id")

        logger.info(
            "Completing PR %s (%s) strategy=%s delete_source=%s",
            pull_request_id,
            f"{project}/{repository}",
            merge_strategy,
            delete_source_branch,
        )
        return self._ado.complete_pull_request(
            project=project,
            repository=repository,
            pull_request_id=pull_request_id,
            last_merge_source_commit=commit,
            merge_strategy=merge_strategy,
            delete_source_branch=delete_source_branch,
        )

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _post_review_comments(
        self,
        project: str,
        repository: str,
        pull_request_id: int,
        review: ReviewResult,
    ) -> None:
        # Summary thread.
        summary = self._format_summary_comment(review)
        self._ado.post_comment_thread(
            project, repository, pull_request_id, summary
        )
        # Inline comments for issues that carry file+line.
        for issue in review.issues:
            if issue.file and issue.line:
                content = (
                    f"**[{issue.severity.upper()}] {issue.title}**\n\n"
                    f"{issue.description}\n\n"
                    f"_Suggestion:_ {issue.suggestion}"
                ).strip()
                try:
                    self._ado.post_comment_thread(
                        project, repository, pull_request_id, content,
                        file_path=issue.file, line=issue.line,
                    )
                except AzureDevOpsError as exc:
                    # Inline anchoring can fail (path/line not in diff). Don't
                    # fail the whole review for that; the summary still lists it.
                    logger.warning(
                        "Inline comment failed for %s:%s (%s); kept in summary",
                        issue.file, issue.line, exc,
                    )

    @staticmethod
    def _format_summary_comment(review: ReviewResult) -> str:
        lines = [
            "## 🤖 Automated PR Review",
            "",
            f"**Decision (LLM):** `{review.decision}`  |  "
            f"**Risk:** `{review.risk_level}`  |  "
            f"**Confidence:** `{review.confidence:.2f}`",
            "",
            review.summary or "_No summary provided._",
        ]
        if review.issues:
            lines += ["", "### Issues"]
            for i in review.issues:
                loc = f" (`{i.file}:{i.line}`)" if i.file and i.line else (
                    f" (`{i.file}`)" if i.file else ""
                )
                lines.append(f"- **{i.severity.upper()}** — {i.title}{loc}: {i.description}")
        if review.approval_blockers:
            lines += ["", "### ⛔ Approval blockers"]
            lines += [f"- {b}" for b in review.approval_blockers]
        if review.positive_notes:
            lines += ["", "### 👍 Positive notes"]
            lines += [f"- {n}" for n in review.positive_notes]
        lines += ["", "_Posted automatically by the PR Review Agent._"]
        return "\n".join(lines)

    def _collect_diff_text(
        self,
        project: str,
        repository: str,
        pull_request_id: int,
        pr: Dict[str, Any],
        changed_paths: List[str],
    ) -> str:
        """Best-effort assembly of reviewable text.

        Azure DevOps does not expose a single unified patch endpoint as cleanly
        as GitHub, so we list changes and pull current file content for changed
        files (capped). This gives the LLM concrete context.
        """
        max_chars = self._settings.app.llm.max_diff_chars
        source_commit = pr.get("lastMergeSourceCommit", {}).get("commitId")
        chunks: List[str] = []
        total = 0
        for path in changed_paths:
            if total >= max_chars:
                chunks.append("\n... (diff truncated: size limit reached) ...")
                break
            try:
                content = self._ado.get_file_content(
                    project, repository, path, version=source_commit
                )
            except AzureDevOpsError:
                content = "(content unavailable)"
            block = f"\n=== FILE: {path} ===\n{content}\n"
            chunks.append(block[: max_chars - total])
            total += len(block)
        return "".join(chunks)

    def _collect_existing_comments(
        self, project: str, repository: str, pull_request_id: int
    ) -> List[str]:
        try:
            threads = self._ado.get_threads(project, repository, pull_request_id)
        except AzureDevOpsError as exc:
            logger.warning("Could not fetch threads for PR %s: %s", pull_request_id, exc)
            return []
        comments: List[str] = []
        for thread in threads:
            for comment in thread.get("comments", []) or []:
                content = (comment.get("content") or "").strip()
                if content:
                    comments.append(content[:500])
        return comments[:50]

    def _collect_status(
        self, project: str, repository: str, pull_request_id: int
    ) -> tuple[Optional[bool], Optional[bool]]:
        """Return (build_ok, policy_ok) as tri-state booleans.

        We treat any policy evaluation that is a build policy specially; all
        other configured policies feed the ``policy_ok`` signal. Unknown =>
        None, which the decision policy treats as failure when required.
        """
        try:
            evals = self._ado.get_policy_evaluations(
                project, repository, pull_request_id
            )
        except AzureDevOpsError as exc:
            logger.warning("Policy evaluations unavailable for PR %s: %s", pull_request_id, exc)
            return None, None
        if not evals:
            return None, None

        build_states: List[bool] = []
        policy_states: List[bool] = []
        for ev in evals:
            status = ev.get("status")  # approved, rejected, queued, running, ...
            cfg = ev.get("configuration", {}) or {}
            type_display = (cfg.get("type", {}) or {}).get("displayName", "").lower()
            ok = status == "approved"
            if "build" in type_display:
                build_states.append(ok)
            else:
                policy_states.append(ok)

        build_ok = all(build_states) if build_states else None
        policy_ok = all(policy_states) if policy_states else None
        return build_ok, policy_ok

    @staticmethod
    def _dry_run_summary(review: ReviewResult, decision: PolicyDecision) -> str:
        return (
            f"[DRY-RUN] would comment={decision.should_comment} "
            f"approve={decision.should_approve} (vote={decision.vote}) "
            f"complete={decision.should_complete}; "
            f"reasons: {'; '.join(decision.reasons) or 'n/a'}"
        )

    def _log_action(
        self,
        project: str,
        repository: str,
        pull_request_id: int,
        action_type: str,
        status: str,
        message: str,
    ) -> None:
        with session_scope() as session:
            record_action(
                session,
                project=project,
                repository=repository,
                pull_request_id=pull_request_id,
                action_type=action_type,
                status=status,
                message=message,
            )

    def _persist(
        self,
        outcome: PRReviewOutcome,
        ctx: PRContext,
        commit_id: str,
        source_branch: str,
        review: Optional[ReviewResult],
    ) -> None:
        with session_scope() as session:
            existing = get_reviewed_for_commit(
                session, ctx.project, ctx.repository,
                ctx.pull_request_id, commit_id,
            )
            row = existing or ReviewedPullRequest(
                project=ctx.project,
                repository=ctx.repository,
                pull_request_id=ctx.pull_request_id,
                latest_commit_id=commit_id,
            )
            row.source_branch = source_branch
            row.target_branch = ctx.target_branch
            row.decision = outcome.decision
            row.confidence = outcome.confidence
            row.risk_level = outcome.risk_level
            row.comment_posted = outcome.commented
            row.approved = outcome.approved
            row.completed = outcome.completed
            row.error_message = outcome.message if outcome.status == "error" else ""
            if existing is None:
                session.add(row)
