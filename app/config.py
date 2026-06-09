"""Configuration loading for the PR review agent.

Two layers of configuration:

1. Environment variables (via ``.env``) -> :class:`EnvSettings`. These hold
   secrets (PAT, API key) and paths.
2. A YAML file -> :class:`AppConfig`. This holds non-secret, declarative
   settings: which projects/repos to scan, the review policy, and so on.

Secrets are *never* stored in YAML. The YAML only names the environment
variables that hold them (``pat_env`` / ``api_key_env``); this module resolves
those names against the process environment.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import List, Optional

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

VALID_MERGE_STRATEGIES = {"no_fast_forward", "squash", "rebase", "rebase_merge"}


class EnvSettings(BaseSettings):
    """Secrets and paths read from the environment / ``.env`` file."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    azure_devops_pat: str = Field(default="", alias="AZURE_DEVOPS_PAT")
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    config_path: str = Field(default="config/targets.yaml", alias="CONFIG_PATH")
    database_path: str = Field(default="data/reviews.db", alias="DATABASE_PATH")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")


class AzureDevOpsConfig(BaseModel):
    base_url: str
    api_version: str = "7.1"
    pat_env: str = "AZURE_DEVOPS_PAT"
    timeout_seconds: int = 30
    max_retries: int = 3

    @field_validator("base_url")
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")


class LLMConfig(BaseModel):
    provider: str = "anthropic"
    model: str = "claude-sonnet-4-6"
    api_key_env: str = "ANTHROPIC_API_KEY"
    max_diff_chars: int = 120_000
    max_tokens: int = 4096
    timeout_seconds: int = 120


class SchedulerConfig(BaseModel):
    enabled: bool = True
    interval_seconds: int = 300


class ReviewPolicy(BaseModel):
    dry_run: bool = True
    auto_comment: bool = True
    auto_approve: bool = False
    auto_complete: bool = False

    minimum_confidence_to_approve: float = 0.90
    minimum_confidence_to_complete: float = 0.95

    require_successful_build: bool = True
    require_successful_policy: bool = True

    max_changed_files: int = 50
    max_diff_lines: int = 3000

    allowed_target_branches: List[str] = Field(default_factory=list)
    risky_paths: List[str] = Field(default_factory=list)

    merge_strategy: str = "squash"
    delete_source_branch_after_merge: bool = False

    @field_validator("merge_strategy")
    @classmethod
    def _validate_strategy(cls, v: str) -> str:
        if v not in VALID_MERGE_STRATEGIES:
            raise ValueError(
                f"merge_strategy must be one of {sorted(VALID_MERGE_STRATEGIES)}, got {v!r}"
            )
        return v


class ReviewerConfig(BaseModel):
    reviewer_id: str


class Target(BaseModel):
    project: str
    repository: str
    enabled: bool = True
    target_branches: List[str] = Field(default_factory=list)


class AppConfig(BaseModel):
    """Top-level YAML configuration."""

    azure_devops: AzureDevOpsConfig
    llm: LLMConfig
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)
    review_policy: ReviewPolicy = Field(default_factory=ReviewPolicy)
    reviewer: ReviewerConfig
    targets: List[Target] = Field(default_factory=list)

    def enabled_targets(self) -> List[Target]:
        return [t for t in self.targets if t.enabled]


def load_app_config(config_path: str) -> AppConfig:
    """Load and validate the YAML config file."""
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Config file not found: {config_path}")
    with open(config_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return AppConfig.model_validate(raw)


class Settings:
    """Bundle of resolved configuration used across the app.

    Resolves the secret env-var *names* declared in YAML into actual values.
    """

    def __init__(self, env: EnvSettings, app: AppConfig) -> None:
        self.env = env
        self.app = app

    @property
    def azure_pat(self) -> str:
        return os.environ.get(self.app.azure_devops.pat_env, self.env.azure_devops_pat)

    @property
    def llm_api_key(self) -> str:
        return os.environ.get(self.app.llm.api_key_env, self.env.anthropic_api_key)

    @property
    def database_path(self) -> str:
        return self.env.database_path


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load settings once and cache them for the process lifetime."""
    env = EnvSettings()
    app = load_app_config(env.config_path)
    return Settings(env=env, app=app)
