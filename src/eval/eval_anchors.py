"""Eval anchors — user-defined ground truth for what 'better' means.

Loaded from ``eval_anchors.yaml`` in the target project root.
Injected into both the LLM judge prompt and the agent improvement prompt
so evaluations align with the user's actual priorities.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class EvalAnchors:
    """User-defined evaluation ground truth."""

    better_means: list[str] = field(default_factory=list)
    worse_means: list[str] = field(default_factory=list)
    must_preserve: list[dict] = field(default_factory=list)
    # Calibration entries added from user feedback on past runs
    calibrations: list[dict] = field(default_factory=list)

    def for_judge_prompt(self) -> str:
        """Format anchors for the LLM judge prompt."""
        if not any([self.better_means, self.worse_means, self.must_preserve, self.calibrations]):
            return ""

        lines = ["## User-Defined Evaluation Anchors",
                 "The project owner has defined what 'improvement' means. "
                 "Score STRICTLY according to these definitions.\n"]

        if self.better_means:
            lines.append("### What counts as BETTER (score > 0.5):")
            for item in self.better_means:
                lines.append(f"  - {item}")

        if self.worse_means:
            lines.append("\n### What counts as WORSE (score < 0.5):")
            for item in self.worse_means:
                lines.append(f"  - {item}")

        if self.must_preserve:
            lines.append("\n### MUST PRESERVE (any violation = score 0.0):")
            for item in self.must_preserve:
                desc = item.get("description", str(item))
                lines.append(f"  - {desc}")

        if self.calibrations:
            lines.append("\n### Calibration from past user feedback:")
            for cal in self.calibrations[-10:]:
                direction = cal.get("direction", "?")
                explanation = cal.get("explanation", "")
                lines.append(f"  - User said this was a FALSE {direction.upper()}: {explanation}")

        return "\n".join(lines)

    def for_agent_prompt(self) -> str:
        """Format anchors for the agent improvement prompt."""
        if not any([self.better_means, self.worse_means, self.must_preserve]):
            return ""

        lines = ["## What the Project Owner Considers an Improvement"]

        if self.better_means:
            lines.append("DO aim for:")
            for item in self.better_means:
                lines.append(f"  - {item}")

        if self.worse_means:
            lines.append("Do NOT do:")
            for item in self.worse_means:
                lines.append(f"  - {item}")

        if self.must_preserve:
            lines.append("MUST preserve (non-negotiable):")
            for item in self.must_preserve:
                desc = item.get("description", str(item))
                lines.append(f"  - {desc}")

        return "\n".join(lines)


def load_eval_anchors(project_root: str) -> EvalAnchors:
    """Load eval_anchors.yaml from the project root. Returns empty anchors if not found."""
    path = Path(project_root) / "eval_anchors.yaml"
    if not path.exists():
        return EvalAnchors()

    try:
        with open(path) as f:
            raw = yaml.safe_load(f) or {}
    except (yaml.YAMLError, OSError):
        return EvalAnchors()

    return EvalAnchors(
        better_means=raw.get("better_means", []),
        worse_means=raw.get("worse_means", []),
        must_preserve=raw.get("must_preserve", []),
    )
