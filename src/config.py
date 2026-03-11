"""Configuration schema and loader for AutoImprove."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator


class Config(BaseModel):
    """Runtime configuration for an AutoImprove run."""

    # Agent
    agent_command: str = "claude"
    agent_timeout_seconds: int = Field(default=300, ge=30)

    # Budgets
    time_budget_minutes: int = Field(ge=1)
    cost_budget_usd: float | None = None
    max_iterations: int | None = None

    # Stop conditions
    max_consecutive_rejections: int = Field(default=5, ge=1)
    max_file_churn: int = Field(default=3, ge=2)
    min_confidence_threshold: float = Field(default=0.3, ge=0.0, le=1.0)

    # Evaluation
    eval_refinement_interval: int = Field(default=5, ge=1)
    llm_judge_model: str = "claude-sonnet"
    llm_judge_runs: int = Field(default=3, ge=1)
    grounding_mode: Literal["interactive", "auto"] = "interactive"
    orchestration_mode: Literal["single", "multi"] = "multi"

    # Targets
    target_paths: list[str]
    exclude_paths: list[str] = Field(
        default_factory=lambda: ["tests/", "node_modules/", ".autoimprove/"]
    )

    # Policy
    protected_paths: list[str] = Field(default_factory=lambda: ["*.lock", "migrations/"])
    max_diff_lines: int = Field(default=500, ge=10)
    allow_dependency_changes: bool = False
    secret_patterns: list[str] = Field(default_factory=lambda: [r"AKIA[0-9A-Z]{16}"])

    # Confidence thresholds per plugin type
    confidence_thresholds: dict[str, float] = Field(
        default_factory=lambda: {"code": 0.6, "workflow": 0.4, "document": 0.3}
    )

    @field_validator("target_paths")
    @classmethod
    def target_paths_non_empty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("target_paths must contain at least one path")
        return v


def load_config(path: str | Path) -> Config:
    """Load and validate configuration from a YAML file."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with open(path) as f:
        raw = yaml.safe_load(f) or {}
    return Config(**raw)


def validate_config(config: Config) -> list[str]:
    """Return a list of validation warnings (empty = all good).

    Pydantic handles hard errors at construction time; this catches
    softer issues worth surfacing to the user.
    """
    warnings: list[str] = []
    if config.time_budget_minutes < 5:
        warnings.append(
            f"time_budget_minutes={config.time_budget_minutes} is very short; "
            "expect few iterations."
        )
    if config.max_diff_lines > 2000:
        warnings.append(
            f"max_diff_lines={config.max_diff_lines} is very large; "
            "consider tightening for safer iterations."
        )
    for path in config.target_paths:
        if path in config.exclude_paths:
            warnings.append(f"'{path}' appears in both target_paths and exclude_paths.")
    return warnings
