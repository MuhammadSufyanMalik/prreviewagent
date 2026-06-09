"""Tests for configuration loading and validation."""

from __future__ import annotations

import textwrap

import pytest

from app.config import AppConfig, ReviewPolicy, load_app_config

VALID_YAML = """
azure_devops:
  base_url: "https://dev.azure.local/DefaultCollection/"
  api_version: "7.1"
  pat_env: "AZURE_DEVOPS_PAT"

llm:
  provider: "anthropic"
  model: "claude-sonnet-4-6"
  api_key_env: "ANTHROPIC_API_KEY"

scheduler:
  enabled: true
  interval_seconds: 120

review_policy:
  dry_run: true
  merge_strategy: "squash"
  allowed_target_branches:
    - "refs/heads/main"

reviewer:
  reviewer_id: "guid-123"

targets:
  - project: "P1"
    repository: "R1"
    enabled: true
    target_branches:
      - "refs/heads/main"
  - project: "P2"
    repository: "R2"
    enabled: false
"""


def test_load_valid_config(tmp_path):
    path = tmp_path / "targets.yaml"
    path.write_text(VALID_YAML)
    cfg = load_app_config(str(path))
    assert isinstance(cfg, AppConfig)
    # trailing slash stripped
    assert cfg.azure_devops.base_url == "https://dev.azure.local/DefaultCollection"
    assert cfg.scheduler.interval_seconds == 120
    assert cfg.reviewer.reviewer_id == "guid-123"


def test_enabled_targets_filters_disabled(tmp_path):
    path = tmp_path / "targets.yaml"
    path.write_text(VALID_YAML)
    cfg = load_app_config(str(path))
    enabled = cfg.enabled_targets()
    assert len(enabled) == 1
    assert enabled[0].project == "P1"


def test_default_safety_flags():
    policy = ReviewPolicy()
    assert policy.dry_run is True
    assert policy.auto_approve is False
    assert policy.auto_complete is False


def test_invalid_merge_strategy_rejected():
    with pytest.raises(ValueError):
        ReviewPolicy(merge_strategy="fast_forward_please")


def test_missing_config_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_app_config(str(tmp_path / "nope.yaml"))
