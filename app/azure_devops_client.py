"""Thin client over the Azure DevOps REST API.

Works against Azure DevOps Services and Azure DevOps Server (on-prem). Auth is
PAT via HTTP Basic (empty username, PAT as password) — the standard scheme for
Azure DevOps. Transient failures (5xx, timeouts, connection errors) are retried
with exponential backoff.

Vote values (Azure DevOps reviewer vote):
    10  Approved
     5  Approved with suggestions
     0  No vote
    -5  Waiting for author
   -10  Rejected
"""

from __future__ import annotations

import base64
import logging
from typing import Any, Dict, List, Optional

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

# Exceptions that are worth retrying (transient).
_RETRYABLE = (httpx.TimeoutException, httpx.TransportError)


class AzureDevOpsError(RuntimeError):
    """Raised for non-retryable API errors (4xx other than throttling)."""


class RetryableStatusError(RuntimeError):
    """Internal marker so tenacity retries on 5xx / 429."""


class AzureDevOpsClient:
    def __init__(
        self,
        base_url: str,
        pat: str,
        api_version: str = "7.1",
        timeout_seconds: int = 30,
        max_retries: int = 3,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_version = api_version
        self._max_retries = max(1, max_retries)
        token = base64.b64encode(f":{pat}".encode()).decode()
        self._client = httpx.Client(
            timeout=timeout_seconds,
            headers={
                "Authorization": f"Basic {token}",
                "Accept": "application/json",
            },
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "AzureDevOpsClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ------------------------------------------------------------------ #
    # Low-level request helper with retries.
    # ------------------------------------------------------------------ #
    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Any] = None,
        api_version: Optional[str] = None,
    ) -> Dict[str, Any]:
        url = f"{self._base_url}/{path.lstrip('/')}"
        params = dict(params or {})
        params.setdefault("api-version", api_version or self._api_version)

        @retry(
            reraise=True,
            stop=stop_after_attempt(self._max_retries),
            wait=wait_exponential(multiplier=1, min=2, max=16),
            retry=retry_if_exception_type(_RETRYABLE + (RetryableStatusError,)),
        )
        def _do() -> Dict[str, Any]:
            resp = self._client.request(method, url, params=params, json=json)
            if resp.status_code in (429, 500, 502, 503, 504):
                logger.warning(
                    "Transient %s from Azure DevOps for %s %s; retrying",
                    resp.status_code,
                    method,
                    path,
                )
                raise RetryableStatusError(f"status={resp.status_code}")
            if resp.status_code >= 400:
                raise AzureDevOpsError(
                    f"{method} {path} -> {resp.status_code}: {resp.text[:500]}"
                )
            if not resp.content:
                return {}
            return resp.json()

        return _do()

    def _repo_path(self, project: str, repository: str, suffix: str) -> str:
        return f"{project}/_apis/git/repositories/{repository}/{suffix}"

    # ------------------------------------------------------------------ #
    # Pull requests
    # ------------------------------------------------------------------ #
    def get_active_pull_requests(
        self, project: str, repository: str, target_branch: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """List active PRs, optionally filtered to a target branch."""
        params: Dict[str, Any] = {"searchCriteria.status": "active"}
        if target_branch:
            params["searchCriteria.targetRefName"] = target_branch
        data = self._request(
            "GET", self._repo_path(project, repository, "pullrequests"), params=params
        )
        return data.get("value", [])

    def get_pull_request(
        self, project: str, repository: str, pull_request_id: int
    ) -> Dict[str, Any]:
        return self._request(
            "GET",
            self._repo_path(project, repository, f"pullrequests/{pull_request_id}"),
        )

    def get_pull_request_iterations(
        self, project: str, repository: str, pull_request_id: int
    ) -> List[Dict[str, Any]]:
        data = self._request(
            "GET",
            self._repo_path(
                project, repository, f"pullrequests/{pull_request_id}/iterations"
            ),
        )
        return data.get("value", [])

    def get_latest_commit_id(
        self, project: str, repository: str, pull_request_id: int
    ) -> Optional[str]:
        """Source commit id of the most recent PR iteration."""
        iterations = self.get_pull_request_iterations(
            project, repository, pull_request_id
        )
        if not iterations:
            return None
        latest = iterations[-1]
        source = latest.get("sourceRefCommit") or {}
        return source.get("commitId")

    def get_changed_files(
        self, project: str, repository: str, pull_request_id: int
    ) -> List[Dict[str, Any]]:
        """Return the change entries of the latest iteration.

        Each entry has ``item.path`` and ``changeType``.
        """
        iterations = self.get_pull_request_iterations(
            project, repository, pull_request_id
        )
        if not iterations:
            return []
        iteration_id = iterations[-1]["id"]
        data = self._request(
            "GET",
            self._repo_path(
                project,
                repository,
                f"pullrequests/{pull_request_id}/iterations/{iteration_id}/changes",
            ),
        )
        return data.get("changeEntries", [])

    def get_file_content(
        self, project: str, repository: str, path: str, version: Optional[str] = None
    ) -> str:
        """Fetch raw file content at a given commit (best effort)."""
        params: Dict[str, Any] = {
            "path": path,
            "includeContent": "true",
            "$format": "text",
        }
        if version:
            params["versionDescriptor.version"] = version
            params["versionDescriptor.versionType"] = "commit"
        url = f"{self._base_url}/{self._repo_path(project, repository, 'items')}"
        params.setdefault("api-version", self._api_version)
        resp = self._client.get(url, params=params)
        if resp.status_code >= 400:
            raise AzureDevOpsError(
                f"get_file_content {path} -> {resp.status_code}: {resp.text[:200]}"
            )
        return resp.text

    def get_diff_summary(
        self,
        project: str,
        repository: str,
        base_commit: str,
        target_commit: str,
    ) -> Dict[str, Any]:
        """Return the diff summary between two commits."""
        params = {
            "baseVersion": base_commit,
            "baseVersionType": "commit",
            "targetVersion": target_commit,
            "targetVersionType": "commit",
            "diffCommonCommit": "true",
        }
        return self._request(
            "GET", self._repo_path(project, repository, "diffs/commits"), params=params
        )

    # ------------------------------------------------------------------ #
    # Threads / comments
    # ------------------------------------------------------------------ #
    def get_threads(
        self, project: str, repository: str, pull_request_id: int
    ) -> List[Dict[str, Any]]:
        data = self._request(
            "GET",
            self._repo_path(
                project, repository, f"pullrequests/{pull_request_id}/threads"
            ),
        )
        return data.get("value", [])

    def post_comment_thread(
        self,
        project: str,
        repository: str,
        pull_request_id: int,
        content: str,
        *,
        file_path: Optional[str] = None,
        line: Optional[int] = None,
        status: str = "active",
    ) -> Dict[str, Any]:
        """Post a comment thread. If ``file_path``/``line`` are given, the
        thread is anchored inline to that file/line (right side)."""
        body: Dict[str, Any] = {
            "comments": [{"parentCommentId": 0, "commentType": 1, "content": content}],
            "status": status,
        }
        if file_path:
            thread_context: Dict[str, Any] = {"filePath": file_path}
            if line is not None:
                thread_context["rightFileStart"] = {"line": line, "offset": 1}
                thread_context["rightFileEnd"] = {"line": line, "offset": 1}
            body["threadContext"] = thread_context
        return self._request(
            "POST",
            self._repo_path(
                project, repository, f"pullrequests/{pull_request_id}/threads"
            ),
            json=body,
        )

    # ------------------------------------------------------------------ #
    # Reviewers / votes
    # ------------------------------------------------------------------ #
    def set_reviewer_vote(
        self,
        project: str,
        repository: str,
        pull_request_id: int,
        reviewer_id: str,
        vote: int,
    ) -> Dict[str, Any]:
        """Cast/Update the reviewer vote. ``vote`` is one of 10/5/0/-5/-10."""
        body = {"vote": vote}
        return self._request(
            "PUT",
            self._repo_path(
                project,
                repository,
                f"pullrequests/{pull_request_id}/reviewers/{reviewer_id}",
            ),
            json=body,
        )

    # ------------------------------------------------------------------ #
    # Policy / build status
    # ------------------------------------------------------------------ #
    def get_policy_evaluations(
        self, project: str, repository: str, pull_request_id: int
    ) -> List[Dict[str, Any]]:
        """Return policy evaluations for the PR artifact.

        Uses the project-scoped policy evaluations endpoint. The PR artifact id
        format is ``vstfs:///CodeReview/CodeReviewId/{projectId}/{prId}``; since
        we may not have the project GUID handy, callers should treat an empty
        result as "unknown".
        """
        pr = self.get_pull_request(project, repository, pull_request_id)
        project_id = (pr.get("repository", {}).get("project", {}) or {}).get("id")
        if not project_id:
            return []
        artifact_id = (
            f"vstfs:///CodeReview/CodeReviewId/{project_id}/{pull_request_id}"
        )
        data = self._request(
            "GET",
            f"{project}/_apis/policy/evaluations",
            params={"artifactId": artifact_id},
        )
        return data.get("value", [])

    # ------------------------------------------------------------------ #
    # Complete / merge
    # ------------------------------------------------------------------ #
    def complete_pull_request(
        self,
        project: str,
        repository: str,
        pull_request_id: int,
        last_merge_source_commit: str,
        merge_strategy: str,
        delete_source_branch: bool,
    ) -> Dict[str, Any]:
        """Complete (merge) the PR.

        ``merge_strategy`` is one of: ``no_fast_forward``, ``squash``,
        ``rebase``, ``rebase_merge`` (mapped to Azure DevOps casing).
        """
        strategy_map = {
            "no_fast_forward": "noFastForward",
            "squash": "squash",
            "rebase": "rebase",
            "rebase_merge": "rebaseMerge",
        }
        ado_strategy = strategy_map.get(merge_strategy)
        if ado_strategy is None:
            raise AzureDevOpsError(f"Unknown merge_strategy: {merge_strategy!r}")

        body = {
            "status": "completed",
            "lastMergeSourceCommit": {"commitId": last_merge_source_commit},
            "completionOptions": {
                "mergeStrategy": ado_strategy,
                "deleteSourceBranch": bool(delete_source_branch),
            },
        }
        return self._request(
            "PATCH",
            self._repo_path(project, repository, f"pullrequests/{pull_request_id}"),
            json=body,
        )
