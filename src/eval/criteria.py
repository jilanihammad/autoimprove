"""Criteria versioning and management.

Criteria define what "better" means for a run.  They are set during the
grounding phase, immutable within a run (v1 policy), and proposals for
changes are captured for human review in the final report.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class CriteriaItem:
    """A single evaluation criterion."""

    name: str
    description: str
    weight: float  # 0.0–1.0; hard gates have weight 0.0
    is_hard_gate: bool = False
    metric_type: str = "deterministic"  # "deterministic" | "judgment"


@dataclass
class CriteriaVersion:
    """A versioned set of evaluation criteria."""

    version: int
    created_at: str
    items: list[CriteriaItem]
    plugin_name: str
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    def to_summary_string(self) -> str:
        """Human-readable summary for inclusion in agent prompts."""
        lines = [f"Criteria v{self.version} ({self.plugin_name}):"]
        for item in self.items:
            tag = "[GATE]" if item.is_hard_gate else f"[w={item.weight:.2f}]"
            lines.append(f"  {tag} {item.name}: {item.description} ({item.metric_type})")
        return "\n".join(lines)


@dataclass
class CriteriaProposal:
    """An agent's proposal to change evaluation criteria."""

    proposed_at: str
    iteration: int
    changes: list[dict]
    rationale: str
    status: str = "pending"


class CriteriaManager:
    """Manages versioned evaluation criteria for a run."""

    def __init__(self, criteria_dir: Path) -> None:
        self.criteria_dir = criteria_dir
        self._versions: dict[int, CriteriaVersion] = {}
        self._proposals: list[CriteriaProposal] = []

    def create_initial(
        self, items: list[CriteriaItem], plugin_name: str, notes: str = ""
    ) -> CriteriaVersion:
        """Create and save version 1 of the criteria."""
        scored = [i for i in items if not i.is_hard_gate]
        total_weight = sum(i.weight for i in scored)
        if scored and abs(total_weight - 1.0) > 0.05:
            # Auto-normalise and warn
            for i in scored:
                i.weight = i.weight / total_weight if total_weight > 0 else 1.0 / len(scored)

        if not items:
            raise ValueError("Criteria must contain at least one item.")

        version = CriteriaVersion(
            version=1,
            created_at=datetime.now(timezone.utc).isoformat(),
            items=items,
            plugin_name=plugin_name,
            notes=notes,
        )
        self._versions[1] = version
        self._save_version(version)
        return version

    def get_current(self) -> CriteriaVersion:
        """Return the highest (current) criteria version."""
        if not self._versions:
            raise RuntimeError("No criteria versions exist. Run grounding first.")
        return self._versions[max(self._versions)]

    def get_version(self, version: int) -> CriteriaVersion:
        """Return a specific criteria version."""
        if version not in self._versions:
            raise KeyError(f"Criteria version {version} not found.")
        return self._versions[version]

    def record_proposal(
        self, iteration: int, changes: list[dict], rationale: str
    ) -> CriteriaProposal:
        """Record a criteria change proposal (not applied in v1)."""
        proposal = CriteriaProposal(
            proposed_at=datetime.now(timezone.utc).isoformat(),
            iteration=iteration,
            changes=changes,
            rationale=rationale,
        )
        self._proposals.append(proposal)
        path = self.criteria_dir / f"proposal_iter_{iteration}.json"
        with open(path, "w") as f:
            json.dump(asdict(proposal), f, indent=2)
        return proposal

    def get_proposals(self) -> list[CriteriaProposal]:
        """Return all proposals sorted by iteration."""
        return sorted(self._proposals, key=lambda p: p.iteration)

    def get_weights_dict(self) -> dict[str, float]:
        """Return ``{name: weight}`` for the current version."""
        return {i.name: i.weight for i in self.get_current().items}

    def get_hard_gates(self) -> list[str]:
        """Return names of hard-gate criteria."""
        return [i.name for i in self.get_current().items if i.is_hard_gate]

    def to_rubric_items(self) -> list[dict]:
        """Convert current criteria to dicts suitable for the LLM judge."""
        return [
            {"name": i.name, "description": i.description, "weight": i.weight}
            for i in self.get_current().items
            if not i.is_hard_gate
        ]

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _save_version(self, version: CriteriaVersion) -> None:
        path = self.criteria_dir / f"criteria_v{version.version}.json"
        with open(path, "w") as f:
            json.dump(version.to_dict(), f, indent=2)

    @classmethod
    def load_all(cls, criteria_dir: Path) -> CriteriaManager:
        """Reconstruct a CriteriaManager from disk."""
        mgr = cls(criteria_dir)
        for p in sorted(criteria_dir.glob("criteria_v*.json")):
            with open(p) as f:
                data = json.load(f)
            items = [CriteriaItem(**i) for i in data["items"]]
            v = CriteriaVersion(
                version=data["version"],
                created_at=data["created_at"],
                items=items,
                plugin_name=data["plugin_name"],
                notes=data.get("notes", ""),
            )
            mgr._versions[v.version] = v
        for p in sorted(criteria_dir.glob("proposal_iter_*.json")):
            with open(p) as f:
                data = json.load(f)
            mgr._proposals.append(CriteriaProposal(**data))
        return mgr

    # ------------------------------------------------------------------
    # Default templates
    # ------------------------------------------------------------------

    @staticmethod
    def default_code_criteria() -> list[CriteriaItem]:
        return [
            CriteriaItem("test_pass_rate", "All tests must pass", 0.0, True, "deterministic"),
            CriteriaItem("build_success", "Project must build", 0.0, True, "deterministic"),
            CriteriaItem("lint_score", "Reduction in lint errors/warnings", 0.20, False, "deterministic"),
            CriteriaItem("complexity", "Reduction in cyclomatic complexity", 0.20, False, "deterministic"),
            CriteriaItem("readability", "Code is easier to understand", 0.25, False, "judgment"),
            CriteriaItem("maintainability", "Code is easier to maintain/extend", 0.20, False, "judgment"),
            CriteriaItem("error_handling", "Proper error handling and edge cases", 0.15, False, "judgment"),
        ]

    @staticmethod
    def default_document_criteria() -> list[CriteriaItem]:
        return [
            CriteriaItem("parseable", "Document is valid/parseable", 0.0, True, "deterministic"),
            CriteriaItem("clarity", "Writing is clear and unambiguous", 0.30, False, "judgment"),
            CriteriaItem("completeness", "All necessary information present", 0.25, False, "judgment"),
            CriteriaItem("structure", "Well-organized with logical flow", 0.20, False, "judgment"),
            CriteriaItem("audience_fit", "Appropriate for target audience", 0.15, False, "judgment"),
            CriteriaItem("actionability", "Reader knows what to do next", 0.10, False, "judgment"),
        ]

    @staticmethod
    def default_workflow_criteria() -> list[CriteriaItem]:
        return [
            CriteriaItem("schema_valid", "Workflow schema is valid", 0.0, True, "deterministic"),
            CriteriaItem("error_handling", "Proper error handling on all nodes", 0.25, False, "judgment"),
            CriteriaItem("efficiency", "Minimal unnecessary nodes/steps", 0.20, False, "judgment"),
            CriteriaItem("reliability", "Handles edge cases and failures", 0.25, False, "judgment"),
            CriteriaItem("readability", "Workflow is easy to understand", 0.15, False, "judgment"),
            CriteriaItem("security", "No exposed secrets or unsafe operations", 0.15, False, "judgment"),
        ]
