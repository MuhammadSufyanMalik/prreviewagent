"""Tests for the PRService orchestration ordering and safety guarantees.

These use a fake Azure DevOps client and a fake review engine so no network or
LLM is involved. The real SQLite layer is used against a temp DB.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from app.azure_devops_client import AzureDevOpsError
from app.config import (
    AppConfig,
    AzureDevOpsConfig,
    LLMConfig,
    ReviewerConfig,
    ReviewPolicy,
    SchedulerConfig,
    Settings,
)
from app.db import init_db
from app.pr_service import PRService
from app.review_engine import Issue, ReviewResult


# ----------------------------- fakes -------------------------------------- #
class FakeADO:
    def __init__(self, *, conflicts=False, build_ok=True, policy_ok=True):
        self.votes: List[int] = []
        self.comments: List[Dict[str, Any]] = []
        self.completed = False
        self._conflicts = conflicts
        self._build_ok = build_ok
        self._policy_ok = policy_ok
        self.fail_comment = False
        self.fail_vote = False

    def get_pull_request(self, project, repository, pull_request_id):
        return {
            "pullRequestId": pull_request_id,
            "sourceRefName": "refs/heads/feature",
            "targetRefName": "refs/heads/main",
            "title": "T",
            "description": "D",
            "mergeStatus": "conflicts" if self._conflicts else "succeeded",
            "lastMergeSourceCommit": {"commitId": "abc123"},
            "repository": {"project": {"id": "proj-guid"}},
        }

    def get_latest_commit_id(self, project, repository, pull_request_id):
        return "abc123"

    def get_changed_files(self, project, repository, pull_request_id):
        return [{"item": {"path": "/src/A.cs"}, "changeType": "edit"}]

    def get_file_content(self, project, repository, path, version=None):
        return "file content"

    def get_threads(self, project, repository, pull_request_id):
        return []

    def get_policy_evaluations(self, project, repository, pull_request_id):
        evals = []
        evals.append({
            "status": "approved" if self._build_ok else "rejected",
            "configuration": {"type": {"displayName": "Build"}},
        })
        evals.append({
            "status": "approved" if self._policy_ok else "rejected",
            "configuration": {"type": {"displayName": "Minimum reviewers"}},
        })
        return evals

    def post_comment_thread(self, project, repository, pull_request_id, content,
                            file_path=None, line=None, status="active"):
        if self.fail_comment:
            raise AzureDevOpsError("comment boom")
        self.comments.append({"content": content, "file": file_path, "line": line})
        return {"id": 1}

    def set_reviewer_vote(self, project, repository, pull_request_id, reviewer_id, vote):
        if self.fail_vote:
            raise AzureDevOpsError("vote boom")
        self.votes.append(vote)
        return {"vote": vote}

    def complete_pull_request(self, **kwargs):
        self.completed = True
        return {"status": "completed"}

    def close(self):
        pass


class FakeEngine:
    def __init__(self, result: ReviewResult):
        self._result = result

    def build_user_prompt(self, **kwargs):
        return "prompt"

    def review(self, prompt):
        return self._result


def make_settings(tmp_path, **policy_overrides) -> Settings:
    policy_defaults = dict(
        dry_run=False, auto_comment=True, auto_approve=True, auto_complete=True,
        minimum_confidence_to_approve=0.90, minimum_confidence_to_complete=0.95,
        require_successful_build=True, require_successful_policy=True,
        max_changed_files=50, max_diff_lines=3000,
        allowed_target_branches=["refs/heads/main"],
        risky_paths=[], merge_strategy="squash",
        delete_source_branch_after_merge=False,
    )
    policy_defaults.update(policy_overrides)
    app = AppConfig(
        azure_devops=AzureDevOpsConfig(base_url="https://x/col"),
        llm=LLMConfig(),
        scheduler=SchedulerConfig(),
        review_policy=ReviewPolicy(**policy_defaults),
        reviewer=ReviewerConfig(reviewer_id="rev-guid"),
        targets=[],
    )

    class _Env:
        database_path = str(tmp_path / "reviews.db")
        log_level = "INFO"
        config_path = ""
        azure_devops_pat = ""
        anthropic_api_key = ""

    s = Settings.__new__(Settings)
    s.env = _Env()
    s.app = app
    return s


def clean_review(confidence=0.99) -> ReviewResult:
    return ReviewResult(decision="approve", confidence=confidence, risk_level="low",
                        summary="ok", issues=[], positive_notes=[], approval_blockers=[])


@pytest.fixture
def db(tmp_path):
    init_db(str(tmp_path / "reviews.db"))


# ----------------------------- tests -------------------------------------- #
def test_happy_path_comment_approve_complete(tmp_path, db):
    settings = make_settings(tmp_path)
    ado = FakeADO()
    svc = PRService(settings, ado, FakeEngine(clean_review()))
    out = svc.review_pull_request("P", "R", 1, force=True)
    assert out.commented is True
    assert out.approved is True
    assert out.completed is True
    assert ado.votes == [10]
    assert ado.completed is True


def test_comment_failure_blocks_approve_and_complete(tmp_path, db):
    settings = make_settings(tmp_path)
    ado = FakeADO()
    ado.fail_comment = True
    svc = PRService(settings, ado, FakeEngine(clean_review()))
    out = svc.review_pull_request("P", "R", 1, force=True)
    assert out.commented is False
    assert out.approved is False
    assert out.completed is False
    assert ado.votes == []
    assert ado.completed is False


def test_approval_failure_blocks_complete(tmp_path, db):
    settings = make_settings(tmp_path)
    ado = FakeADO()
    ado.fail_vote = True
    svc = PRService(settings, ado, FakeEngine(clean_review()))
    out = svc.review_pull_request("P", "R", 1, force=True)
    assert out.commented is True
    assert out.approved is False
    assert out.completed is False
    assert ado.completed is False


def test_conflicts_block_complete(tmp_path, db):
    settings = make_settings(tmp_path)
    ado = FakeADO(conflicts=True)
    svc = PRService(settings, ado, FakeEngine(clean_review()))
    out = svc.review_pull_request("P", "R", 1, force=True)
    # conflicts make the decision policy block complete
    assert out.completed is False


def test_failing_build_blocks_complete(tmp_path, db):
    settings = make_settings(tmp_path)
    ado = FakeADO(build_ok=False)
    svc = PRService(settings, ado, FakeEngine(clean_review()))
    out = svc.review_pull_request("P", "R", 1, force=True)
    assert out.completed is False


def test_dry_run_takes_no_actions(tmp_path, db):
    settings = make_settings(tmp_path, dry_run=True)
    ado = FakeADO()
    svc = PRService(settings, ado, FakeEngine(clean_review()))
    out = svc.review_pull_request("P", "R", 1, force=True)
    assert out.status == "dry_run"
    assert ado.comments == []
    assert ado.votes == []
    assert ado.completed is False


def test_dry_run_param_overrides_config(tmp_path, db):
    settings = make_settings(tmp_path, dry_run=False)
    ado = FakeADO()
    svc = PRService(settings, ado, FakeEngine(clean_review()))
    out = svc.review_pull_request("P", "R", 1, dry_run=True, force=True)
    assert out.status == "dry_run"
    assert ado.completed is False


def test_already_reviewed_commit_skipped(tmp_path, db):
    settings = make_settings(tmp_path, dry_run=True)
    ado = FakeADO()
    svc = PRService(settings, ado, FakeEngine(clean_review()))
    svc.review_pull_request("P", "R", 1, force=True)  # records review
    out = svc.review_pull_request("P", "R", 1, force=False)
    assert out.status == "skipped"


def test_critical_issue_comments_but_no_approve(tmp_path, db):
    settings = make_settings(tmp_path)
    ado = FakeADO()
    review = ReviewResult(
        decision="reject", confidence=0.99, risk_level="critical",
        summary="bad", issues=[Issue(severity="critical", title="rce")],
        positive_notes=[], approval_blockers=["fix rce"],
    )
    svc = PRService(settings, ado, FakeEngine(review))
    out = svc.review_pull_request("P", "R", 1, force=True)
    assert out.commented is True
    assert out.approved is False
    assert out.completed is False
    assert ado.votes == []
