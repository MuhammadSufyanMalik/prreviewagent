"""Unit tests for the deterministic decision policy."""

from __future__ import annotations

import pytest

from app.config import ReviewPolicy
from app.decision_policy import (
    VOTE_APPROVE,
    VOTE_APPROVE_WITH_SUGGESTIONS,
    VOTE_NO_VOTE,
    VOTE_REJECT,
    VOTE_WAIT_FOR_AUTHOR,
    PRContext,
    compute_vote,
    evaluate,
)
from app.review_engine import Issue, ReviewResult


def make_policy(**overrides) -> ReviewPolicy:
    base = dict(
        dry_run=False,
        auto_comment=True,
        auto_approve=True,
        auto_complete=True,
        minimum_confidence_to_approve=0.90,
        minimum_confidence_to_complete=0.95,
        require_successful_build=True,
        require_successful_policy=True,
        max_changed_files=50,
        max_diff_lines=3000,
        allowed_target_branches=["refs/heads/main", "refs/heads/develop"],
        risky_paths=["auth", "payment", "migrations"],
        merge_strategy="squash",
        delete_source_branch_after_merge=False,
    )
    base.update(overrides)
    return ReviewPolicy(**base)


def make_ctx(**overrides) -> PRContext:
    base = dict(
        project="P",
        repository="R",
        pull_request_id=1,
        target_branch="refs/heads/main",
        changed_files_count=3,
        diff_lines=120,
        changed_paths=["src/A.cs", "src/B.cs"],
        build_succeeded=True,
        policy_succeeded=True,
        has_conflicts=False,
    )
    base.update(overrides)
    return PRContext(**base)


def make_review(decision="approve", confidence=0.99, risk="low", issues=None,
                blockers=None) -> ReviewResult:
    return ReviewResult(
        decision=decision,
        confidence=confidence,
        risk_level=risk,
        summary="ok",
        issues=issues or [],
        positive_notes=[],
        approval_blockers=blockers or [],
    )


# --------------------------- compute_vote --------------------------------- #
def test_vote_reject_on_critical():
    review = make_review(issues=[Issue(severity="critical", title="x")])
    assert compute_vote(review, make_policy()) == VOTE_REJECT


def test_vote_wait_on_high():
    review = make_review(issues=[Issue(severity="high", title="x")])
    assert compute_vote(review, make_policy()) == VOTE_WAIT_FOR_AUTHOR


def test_vote_wait_on_blockers():
    review = make_review(blockers=["must fix tests"])
    assert compute_vote(review, make_policy()) == VOTE_WAIT_FOR_AUTHOR


def test_vote_no_vote_on_medium():
    review = make_review(issues=[Issue(severity="medium", title="x")])
    assert compute_vote(review, make_policy()) == VOTE_NO_VOTE


def test_vote_approve_with_suggestions_on_low():
    review = make_review(issues=[Issue(severity="low", title="x")])
    assert compute_vote(review, make_policy()) == VOTE_APPROVE_WITH_SUGGESTIONS


def test_vote_approve_clean():
    review = make_review(issues=[])
    assert compute_vote(review, make_policy()) == VOTE_APPROVE


# ----------------------------- evaluate ----------------------------------- #
def test_clean_pr_approves_and_completes():
    d = evaluate(make_review(), make_ctx(), make_policy())
    assert d.should_comment is True
    assert d.should_approve is True
    assert d.should_complete is True
    assert d.vote == VOTE_APPROVE


def test_critical_blocks_everything_but_comment():
    review = make_review(issues=[Issue(severity="critical", title="rce")])
    d = evaluate(review, make_ctx(), make_policy())
    assert d.should_comment is True
    assert d.should_approve is False
    assert d.should_complete is False
    assert d.vote == VOTE_REJECT


def test_low_confidence_blocks_approval():
    d = evaluate(make_review(confidence=0.80), make_ctx(), make_policy())
    assert d.should_approve is False
    assert d.should_complete is False


def test_medium_issue_approval_with_suggestions_but_no_complete():
    review = make_review(issues=[Issue(severity="medium", title="null")])
    d = evaluate(review, make_ctx(), make_policy())
    # medium -> NO_VOTE so not approvable, definitely not completable
    assert d.vote == VOTE_NO_VOTE
    assert d.should_approve is False
    assert d.should_complete is False


def test_low_issue_approves_but_does_not_complete():
    review = make_review(issues=[Issue(severity="low", title="style")])
    d = evaluate(review, make_ctx(), make_policy())
    assert d.vote == VOTE_APPROVE_WITH_SUGGESTIONS
    assert d.should_approve is True
    # complete requires full approve vote
    assert d.should_complete is False


def test_disallowed_branch_blocks_all_actions():
    d = evaluate(make_review(), make_ctx(target_branch="refs/heads/feature"),
                 make_policy())
    assert d.should_approve is False
    assert d.should_complete is False
    assert any("not in allowed" in b for b in d.blockers)


def test_too_large_pr_blocks():
    d = evaluate(make_review(), make_ctx(changed_files_count=200), make_policy())
    assert d.should_approve is False
    assert d.should_complete is False


def test_unknown_build_blocks_complete_when_required():
    d = evaluate(make_review(), make_ctx(build_succeeded=None), make_policy())
    assert d.should_approve is True  # approve doesn't need build
    assert d.should_complete is False


def test_failed_policy_blocks_complete():
    d = evaluate(make_review(), make_ctx(policy_succeeded=False), make_policy())
    assert d.should_complete is False


def test_conflicts_block_complete():
    d = evaluate(make_review(), make_ctx(has_conflicts=True), make_policy())
    assert d.should_complete is False


def test_risky_paths_block_complete():
    ctx = make_ctx(changed_paths=["src/auth/Login.cs"])
    d = evaluate(make_review(), ctx, make_policy())
    assert d.should_complete is False
    assert any("risky" in r for r in d.reasons)


def test_auto_approve_disabled_means_no_approve():
    d = evaluate(make_review(), make_ctx(), make_policy(auto_approve=False))
    assert d.should_approve is False
    assert d.should_complete is False


def test_auto_complete_disabled_means_no_complete():
    d = evaluate(make_review(), make_ctx(), make_policy(auto_complete=False))
    assert d.should_approve is True
    assert d.should_complete is False


def test_low_complete_confidence_blocks_complete():
    # passes approve threshold (0.90) but not complete threshold (0.95)
    d = evaluate(make_review(confidence=0.92), make_ctx(), make_policy())
    assert d.should_approve is True
    assert d.should_complete is False
