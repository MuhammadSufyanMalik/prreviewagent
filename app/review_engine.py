"""Build the review prompt, call the LLM, and parse strict JSON output.

The LLM is asked to return JSON only. We still parse defensively (stripping
code fences, locating the first JSON object) because models occasionally wrap
output. The parsed result is validated into :class:`ReviewResult`.
"""

from __future__ import annotations

import json
import logging
import re
from typing import List, Optional

from pydantic import BaseModel, Field, ValidationError, field_validator

from app.llm_client import LLMClient

logger = logging.getLogger(__name__)

VALID_DECISIONS = {
    "comment_only",
    "approve",
    "approve_with_suggestions",
    "wait_for_author",
    "reject",
    "approve_and_complete",
}
VALID_SEVERITIES = {"info", "low", "medium", "high", "critical"}
VALID_RISK_LEVELS = {"low", "medium", "high", "critical"}


class Issue(BaseModel):
    severity: str = "info"
    file: Optional[str] = None
    line: Optional[int] = None
    title: str = ""
    description: str = ""
    suggestion: str = ""

    @field_validator("severity")
    @classmethod
    def _norm_severity(cls, v: str) -> str:
        v = (v or "info").lower()
        return v if v in VALID_SEVERITIES else "info"


class ReviewResult(BaseModel):
    decision: str
    confidence: float = 0.0
    risk_level: str = "low"
    summary: str = ""
    issues: List[Issue] = Field(default_factory=list)
    positive_notes: List[str] = Field(default_factory=list)
    approval_blockers: List[str] = Field(default_factory=list)

    @field_validator("decision")
    @classmethod
    def _validate_decision(cls, v: str) -> str:
        v = (v or "").strip()
        if v not in VALID_DECISIONS:
            raise ValueError(f"decision must be one of {sorted(VALID_DECISIONS)}")
        return v

    @field_validator("risk_level")
    @classmethod
    def _norm_risk(cls, v: str) -> str:
        v = (v or "low").lower()
        return v if v in VALID_RISK_LEVELS else "low"

    @field_validator("confidence")
    @classmethod
    def _clamp_confidence(cls, v: float) -> float:
        return max(0.0, min(1.0, float(v)))

    def max_severity(self) -> str:
        order = ["info", "low", "medium", "high", "critical"]
        worst = "info"
        for issue in self.issues:
            if order.index(issue.severity) > order.index(worst):
                worst = issue.severity
        return worst

    def has_severity(self, severity: str) -> bool:
        return any(i.severity == severity for i in self.issues)


SYSTEM_PROMPT = """You are a meticulous senior software engineer performing a \
code review of an Azure DevOps pull request. You are conservative and \
safety-focused: you would rather flag a concern than let a defect ship.

Return ONLY a single JSON object, no prose, no markdown fences. Schema:
{
  "decision": one of ["comment_only","approve","approve_with_suggestions","wait_for_author","reject","approve_and_complete"],
  "confidence": number between 0 and 1,
  "risk_level": one of ["low","medium","high","critical"],
  "summary": string,
  "issues": [
    {"severity": one of ["info","low","medium","high","critical"],
     "file": string, "line": integer, "title": string,
     "description": string, "suggestion": string}
  ],
  "positive_notes": [string],
  "approval_blockers": [string]
}

Guidance:
- Use "critical" for security holes, data loss, broken auth, or anything unsafe to merge.
- Use "high" for correctness bugs that should block approval.
- Put anything that must be resolved before merge into "approval_blockers".
- Be precise about file and line numbers when you can infer them from the diff.
- Only choose "approve" or "approve_and_complete" when you are genuinely confident the change is safe."""


class ReviewEngine:
    def __init__(self, llm: LLMClient, max_diff_chars: int = 120_000) -> None:
        self._llm = llm
        self._max_diff_chars = max_diff_chars

    def build_user_prompt(
        self,
        *,
        title: str,
        description: str,
        source_branch: str,
        target_branch: str,
        changed_files: List[str],
        diff_text: str,
        existing_comments: Optional[List[str]] = None,
    ) -> str:
        diff_text = diff_text[: self._max_diff_chars]
        files_block = "\n".join(f"- {f}" for f in changed_files) or "(none reported)"
        comments_block = (
            "\n".join(f"- {c}" for c in existing_comments)
            if existing_comments
            else "(none)"
        )
        return (
            f"# Pull Request\n"
            f"Title: {title}\n"
            f"Source: {source_branch} -> Target: {target_branch}\n\n"
            f"## Description\n{description or '(no description)'}\n\n"
            f"## Changed files ({len(changed_files)})\n{files_block}\n\n"
            f"## Existing review comments\n{comments_block}\n\n"
            f"## Diff / content\n```\n{diff_text}\n```\n\n"
            f"Review this pull request and respond with the JSON object only."
        )

    def review(self, user_prompt: str) -> ReviewResult:
        raw = self._llm.complete(SYSTEM_PROMPT, user_prompt)
        return self.parse_result(raw)

    @staticmethod
    def parse_result(raw: str) -> ReviewResult:
        text = _extract_json(raw)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"LLM did not return valid JSON: {exc}") from exc
        try:
            return ReviewResult.model_validate(data)
        except ValidationError as exc:
            raise ValueError(f"LLM JSON failed validation: {exc}") from exc


def _extract_json(raw: str) -> str:
    """Strip code fences and isolate the first top-level JSON object."""
    text = raw.strip()
    # Remove ```json ... ``` fences if present.
    fence = re.match(r"^```[a-zA-Z]*\n(.*)\n```$", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    # Find first '{' and matching last '}'.
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]
    return text
