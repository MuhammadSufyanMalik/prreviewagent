"""Deterministic decision policy.

This module is the safety backstop. It does **not** trust the LLM's ``decision``
field blindly. Instead it takes the parsed :class:`ReviewResult` plus objective
facts about the PR (size, branch, build/policy status, risky files) and the
configured :class:`ReviewPolicy`, and computes what the agent is actually
allowed to do: comment, approve (with what vote), and complete.

Vote values map to Azure DevOps reviewer votes:
    APPROVE                = 10
    APPROVE_WITH_SUGGEST   = 5
    NO_VOTE                = 0
    WAIT_FOR_AUTHOR        = -5
    REJECT                 = -10
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from app.config import ReviewPolicy
from app.review_engine import ReviewResult

# Azure DevOps vote constants.
VOTE_APPROVE = 10
VOTE_APPROVE_WITH_SUGGESTIONS = 5
VOTE_NO_VOTE = 0
VOTE_WAIT_FOR_AUTHOR = -5
VOTE_REJECT = -10


@dataclass
class PRContext:
    """Objective facts about a PR, independent of the LLM's opinion."""

    project: str
    repository: str
    pull_request_id: int
    target_branch: str
    changed_files_count: int
    diff_lines: int
    changed_paths: List[str] = field(default_factory=list)
    # Tri-state: True/False known, None = unknown/unavailable.
    build_succeeded: Optional[bool] = None
    policy_succeeded: Optional[bool] = None
    has_conflicts: Optional[bool] = None

    def risky_paths_hit(self, risky_substrings: List[str]) -> List[str]:
        hits: List[str] = []
        lowered = [(p, p.lower()) for p in self.changed_paths]
        for needle in risky_substrings:
            n = needle.lower()
            for original, low in lowered:
                if n in low and original not in hits:
                    hits.append(original)
        return hits


@dataclass
class PolicyDecision:
    """Result of evaluating the policy."""

    should_comment: bool = False
    should_approve: bool = False
    should_complete: bool = False
    vote: int = VOTE_NO_VOTE
    reasons: List[str] = field(default_factory=list)
    blockers: List[str] = field(default_factory=list)

    def add_reason(self, msg: str) -> None:
        self.reasons.append(msg)

    def add_blocker(self, msg: str) -> None:
        self.blockers.append(msg)


def compute_vote(review: ReviewResult, policy: ReviewPolicy) -> int:
    """Map review findings to an Azure DevOps vote, deterministically.

    This deliberately ignores the LLM's ``decision`` for the *vote*; severity of
    issues and approval blockers drive it.
    """
    if review.has_severity("critical"):
        return VOTE_REJECT
    if review.has_severity("high"):
        return VOTE_WAIT_FOR_AUTHOR
    if review.approval_blockers:
        return VOTE_WAIT_FOR_AUTHOR
    if review.has_severity("medium"):
        # Medium issues: surface them but do not approve.
        return VOTE_NO_VOTE
    # Only low/info issues (or none).
    if review.issues:
        return VOTE_APPROVE_WITH_SUGGESTIONS
    return VOTE_APPROVE


def evaluate(
    review: ReviewResult, ctx: PRContext, policy: ReviewPolicy
) -> PolicyDecision:
    """Compute the agent's allowed actions from review + facts + config.

    Order of operations:
      1. Validate hard gates (branch, size). Failing these blocks everything.
      2. Decide comment (always allowed if auto_comment).
      3. Decide vote and whether to approve.
      4. Decide whether to complete (strictest gate).
    """
    decision = PolicyDecision()

    # --- Hard gates that affect approval/merge eligibility -------------- #
    branch_allowed = (
        not policy.allowed_target_branches
        or ctx.target_branch in policy.allowed_target_branches
    )
    if not branch_allowed:
        decision.add_blocker(
            f"target branch {ctx.target_branch} not in allowed list"
        )

    size_ok = (
        ctx.changed_files_count <= policy.max_changed_files
        and ctx.diff_lines <= policy.max_diff_lines
    )
    if not size_ok:
        decision.add_blocker(
            f"PR too large: {ctx.changed_files_count} files / {ctx.diff_lines} "
            f"lines exceeds limit ({policy.max_changed_files}/{policy.max_diff_lines})"
        )

    risky_hits = ctx.risky_paths_hit(policy.risky_paths)
    if risky_hits:
        decision.add_reason(f"risky paths touched: {', '.join(risky_hits)}")

    # --- 1. Comment ----------------------------------------------------- #
    decision.should_comment = policy.auto_comment
    if policy.auto_comment:
        decision.add_reason("commenting enabled")

    # --- 2. Vote / approve ---------------------------------------------- #
    vote = compute_vote(review, policy)
    decision.vote = vote

    approve_eligible = vote in (VOTE_APPROVE, VOTE_APPROVE_WITH_SUGGESTIONS)
    if not approve_eligible:
        decision.add_reason(
            f"vote {vote} is not an approval (issues/blockers present)"
        )

    confidence_ok_approve = review.confidence >= policy.minimum_confidence_to_approve
    if not confidence_ok_approve:
        decision.add_reason(
            f"confidence {review.confidence:.2f} below approve threshold "
            f"{policy.minimum_confidence_to_approve:.2f}"
        )

    can_approve = (
        policy.auto_approve
        and approve_eligible
        and confidence_ok_approve
        and branch_allowed
        and size_ok
        and not decision.blockers
    )
    decision.should_approve = can_approve
    if policy.auto_approve and not can_approve:
        decision.add_reason("approval withheld by policy checks")

    # --- 3. Complete / merge (strictest) -------------------------------- #
    # Every condition below must hold. Unknown build/policy (None) counts as
    # failure when the corresponding requirement is enabled.
    complete_checks: List[tuple[str, bool]] = []
    complete_checks.append(("auto_complete enabled", policy.auto_complete))
    complete_checks.append(("agent will approve first", can_approve))
    complete_checks.append(("vote is full approve", vote == VOTE_APPROVE))
    complete_checks.append(("no critical issues", not review.has_severity("critical")))
    complete_checks.append(("no high issues", not review.has_severity("high")))
    complete_checks.append(("no medium issues", not review.has_severity("medium")))
    complete_checks.append(("no approval blockers", not review.approval_blockers))
    complete_checks.append(
        (
            f"confidence >= {policy.minimum_confidence_to_complete}",
            review.confidence >= policy.minimum_confidence_to_complete,
        )
    )
    complete_checks.append(("branch allowed", branch_allowed))
    complete_checks.append(("size within limit", size_ok))
    complete_checks.append(("no merge conflicts", ctx.has_conflicts is not True))
    complete_checks.append(("no risky paths touched", not risky_hits))

    if policy.require_successful_build:
        complete_checks.append(("build succeeded", ctx.build_succeeded is True))
    if policy.require_successful_policy:
        complete_checks.append(("policy succeeded", ctx.policy_succeeded is True))

    failed = [name for name, ok in complete_checks if not ok]
    decision.should_complete = policy.auto_complete and not failed
    for name in failed:
        if policy.auto_complete:
            decision.add_reason(f"complete blocked: {name} not satisfied")

    return decision
