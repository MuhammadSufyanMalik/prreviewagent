"""Structured logging setup.

Logs are line-oriented and include a timestamp, level, logger name and message.
A redaction filter scrubs anything that looks like a token/PAT so secrets never
land in the logs.
"""

from __future__ import annotations

import logging
import re
import sys

# Patterns that should never appear in logs. We redact obvious secret-bearing
# query params and Bearer/Basic auth headers.
_REDACTION_PATTERNS = [
    re.compile(r"(api-version=)([^&\s]+)"),  # harmless, kept readable
]
_SECRET_PATTERNS = [
    re.compile(r"(Authorization:\s*)(Basic|Bearer)\s+\S+", re.IGNORECASE),
    re.compile(r"(pat[=:]\s*)\S+", re.IGNORECASE),
    re.compile(r"(token[\"']?\s*[:=]\s*[\"']?)[A-Za-z0-9._\-]{12,}", re.IGNORECASE),
]


class RedactionFilter(logging.Filter):
    """Scrub secret-like substrings from log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self._redact(record.msg)
        if record.args:
            record.args = tuple(
                self._redact(a) if isinstance(a, str) else a for a in record.args
            )
        return True

    @staticmethod
    def _redact(text: str) -> str:
        for pattern in _SECRET_PATTERNS:
            text = pattern.sub(r"\1***REDACTED***", text)
        return text


def configure_logging(level: str = "INFO") -> None:
    """Configure root logging. Idempotent."""
    root = logging.getLogger()
    root.setLevel(level.upper())

    # Avoid duplicate handlers on reload.
    for h in list(root.handlers):
        root.removeHandler(h)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
    )
    handler.addFilter(RedactionFilter())
    root.addHandler(handler)

    # Quiet noisy third-party loggers.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
