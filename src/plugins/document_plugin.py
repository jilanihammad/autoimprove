"""Document evaluator plugin — docs, spreadsheets, presentations.

LOW confidence — primarily LLM-judgment-based.  The system is honest
that document evaluation is less reliable than code evaluation.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path

from src.plugins.base import EvaluatorPlugin, GuardrailConfig, PluginPreflightResult
from src.types import BaselineSnapshot, ConfidenceProfile, DeltaSummary, Diff, GateResult, SoftEvalResult

_DOC_EXTENSIONS = frozenset({".md", ".mdx", ".txt", ".rst", ".csv"})
_RICH_DOC_EXTENSIONS = frozenset({".docx", ".xlsx", ".xls", ".pptx"})
_SKIP_NAMES = frozenset({"README.md", "CHANGELOG.md", "LICENSE", "LICENSE.md"})


class DocumentPlugin(EvaluatorPlugin):
    """Evaluates documents primarily via LLM judgment."""

    @property
    def name(self) -> str:
        return "document"

    @property
    def confidence_profile(self) -> ConfidenceProfile:
        return ConfidenceProfile.LOW

    @property
    def description(self) -> str:
        return "Evaluates documents, spreadsheets, and presentations via LLM judgment."

    def discover_targets(self, paths: list[str], exclude: list[str]) -> list[str]:
        targets: list[str] = []
        all_ext = _DOC_EXTENSIONS | _RICH_DOC_EXTENSIONS
        for p in paths:
            pp = Path(p)
            files = pp.rglob("*") if pp.is_dir() else [pp]
            for fp in files:
                if not fp.is_file() or fp.suffix not in all_ext:
                    continue
                if fp.name in _SKIP_NAMES:
                    continue
                rel = str(fp)
                if any(fnmatch(rel, ex) or fnmatch(fp.name, ex) for ex in exclude):
                    continue
                targets.append(str(fp))
        return targets

    def preflight(self, targets: list[str]) -> PluginPreflightResult:
        warnings: list[str] = []
        rich_docs = [t for t in targets if Path(t).suffix in _RICH_DOC_EXTENSIONS]
        if rich_docs:
            try:
                import docx  # noqa: F401
            except ImportError:
                warnings.append("python-docx not installed — .docx files will be skipped")
            try:
                import openpyxl  # noqa: F401
            except ImportError:
                warnings.append("openpyxl not installed — .xlsx files will be skipped")

        return PluginPreflightResult(
            passed=True,
            available_tools=["text"],
            warnings=warnings or ["Document evaluation relies primarily on LLM judgment"],
        )

    def baseline(self, targets: list[str], working_dir: str) -> BaselineSnapshot:
        metrics: dict[str, float] = {}
        total_words = 0
        total_structure = 0.0

        for t in targets:
            text = self._read_document_text(t)
            total_words += len(text.split())
            total_structure += self._compute_structure_score(text)

        metrics["total_words"] = float(total_words)
        metrics["avg_structure_score"] = total_structure / max(len(targets), 1)
        metrics["document_count"] = float(len(targets))

        return BaselineSnapshot(
            plugin_name=self.name,
            timestamp=datetime.now(timezone.utc).isoformat(),
            metrics=metrics,
            raw_data={},
            targets=targets,
        )

    def hard_gates(self, diff: Diff, targets: list[str], working_dir: str) -> GateResult:
        gates: dict[str, bool] = {}
        failures: list[str] = []

        for t in targets:
            fp = Path(t)
            if not fp.exists():
                continue
            # Gate: parseable and not empty
            text = self._read_document_text(t)
            ok = len(text.strip()) > 0
            gates[f"not_empty:{fp.name}"] = ok
            if not ok:
                failures.append(f"{fp.name} is empty or unreadable")

        return GateResult(
            all_passed=all(gates.values()) if gates else True,
            gates=gates,
            failures=failures,
        )

    def soft_evaluate(
        self, diff: Diff, targets: list[str], criteria: dict, working_dir: str
    ) -> SoftEvalResult:
        scores: dict[str, float] = {}
        total_words = 0
        total_structure = 0.0
        count = 0

        for t in targets:
            if not Path(t).exists():
                continue
            text = self._read_document_text(t)
            total_words += len(text.split())
            total_structure += self._compute_structure_score(text)
            count += 1

        scores["word_count"] = float(total_words)
        scores["structure_score"] = total_structure / max(count, 1)

        return SoftEvalResult(
            scores=scores,
            has_deterministic=False,
            composite=scores.get("structure_score", 0.5),
        )

    def summarize_delta(self, baseline: BaselineSnapshot, current: BaselineSnapshot) -> DeltaSummary:
        improved: dict[str, tuple[float, float]] = {}
        regressed: dict[str, tuple[float, float]] = {}
        unchanged: list[str] = []

        for key in baseline.metrics:
            before = baseline.metrics[key]
            after = current.metrics.get(key, before)
            if abs(after - before) < 0.001:
                unchanged.append(key)
            elif after > before:
                improved[key] = (before, after)
            else:
                regressed[key] = (before, after)

        return DeltaSummary(
            plugin_name=self.name,
            improved=improved,
            regressed=regressed,
            unchanged=unchanged,
            summary_text=f"{len(improved)} improved, {len(regressed)} regressed",
        )

    def guardrails(self) -> GuardrailConfig:
        return GuardrailConfig()

    def deterministic_metric_reliability(self) -> float:
        return 0.15  # word count and structure score are weak signals

    def build_judge_prompt(
        self, current_snapshot: str, candidate_diff: str, rubric_text: str, eval_anchors: str,
    ) -> str | None:
        return f"""You are an expert document reviewer. Evaluate whether the proposed changes improve this document.

Focus on:
- Clarity: Is the argument clearer and easier to follow?
- Completeness: Are important points covered without unnecessary padding?
- Structure: Are headings, sections, and flow logical?
- Accuracy: Are claims well-supported and factually correct?

{eval_anchors}

## Current Document
{current_snapshot}

## Proposed Changes (diff)
{candidate_diff}

## Evaluation Criteria
{rubric_text}

Respond ONLY with JSON (no markdown fences):
{{
  "scores": [
    {{"rubric_item": "criterion_name", "score": 0.0, "reasoning": "explanation"}},
    ...
  ]
}}"""

    def judge_perspectives(self) -> list[dict] | None:
        return [
            {"role": "domain expert", "instruction": "Evaluate technical accuracy and depth. Is the content correct and thorough?"},
            {"role": "editor", "instruction": "Evaluate clarity, conciseness, and structure. Is this well-written and easy to follow?"},
            {"role": "target reader", "instruction": "Evaluate usefulness. Would the intended audience find this helpful and actionable?"},
        ]

    # ------------------------------------------------------------------
    # Prompt templates
    # ------------------------------------------------------------------

    def indexer_prompt_hint(self) -> str:
        return (
            "For each document, summarize:\n"
            "- **Purpose**: What this document covers and its intended audience\n"
            "- **Structure**: How it's organized (sections, headings, flow)\n"
            "- **Content quality**: Clarity, completeness, readability\n"
            "- **Issues**: Outdated info, gaps, redundancy, poor formatting"
        )

    def analyst_categories(self) -> list[dict[str, str]]:
        return [
            {"name": "clarity", "description": "Unclear or confusing explanations"},
            {"name": "structure", "description": "Poor organization or heading hierarchy"},
            {"name": "completeness", "description": "Missing information or examples"},
            {"name": "redundancy", "description": "Repeated or unnecessary content"},
            {"name": "accuracy", "description": "Outdated or incorrect information"},
            {"name": "formatting", "description": "Inconsistent or poor formatting"},
        ]

    def analyst_role(self) -> str:
        return "a senior technical writer and editor"

    def modifier_role(self) -> str:
        return "You are an expert technical writer and editor."

    def modifier_constraints(self) -> list[str]:
        return [
            "Make exactly ONE focused change to improve the document.",
            "Preserve the original meaning and technical accuracy.",
            "Do NOT add filler words or marketing language.",
            "Do NOT remove technical detail that practitioners need.",
            "Keep changes minimal — improve one section, not the whole document.",
        ]

    def reviewer_focus(self) -> str:
        return (
            "Evaluate whether this change improves the document. Consider:\n"
            "1. Is the content clearer and easier to follow?\n"
            "2. Are important points covered without unnecessary padding?\n"
            "3. Is the structure logical with proper headings and flow?\n"
            "4. Are claims well-supported and factually correct?\n"
            "5. Would the intended audience find this more helpful?"
        )

    def theme_map(self) -> dict[str, tuple[str, str, str]]:
        return {
            "clarity": ("Clarity & Readability", "📖", "Documents are easier to understand and follow."),
            "structure": ("Structure & Organization", "🏗️", "Logical flow with clear headings and sections."),
            "completeness": ("Completeness", "📋", "All necessary information and examples are present."),
            "redundancy": ("Conciseness", "✂️", "Redundant content removed, every sentence earns its place."),
            "accuracy": ("Accuracy & Currency", "🎯", "Information is correct and up to date."),
            "formatting": ("Formatting & Consistency", "🎨", "Consistent style, proper formatting throughout."),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _read_document_text(self, path: str) -> str:
        fp = Path(path)
        if fp.suffix in _DOC_EXTENSIONS:
            try:
                return fp.read_text(errors="ignore")
            except OSError:
                return ""
        if fp.suffix == ".docx":
            try:
                import docx
                doc = docx.Document(path)
                return "\n".join(p.text for p in doc.paragraphs)
            except Exception:
                return ""
        if fp.suffix in (".xlsx", ".xls"):
            try:
                import openpyxl
                wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
                texts = []
                for ws in wb.worksheets:
                    for row in ws.iter_rows(values_only=True):
                        texts.append(" ".join(str(c) for c in row if c is not None))
                return "\n".join(texts)
            except Exception:
                return ""
        return ""

    def _compute_structure_score(self, text: str) -> float:
        """Simple structure heuristic: headings, lists, paragraphs."""
        if not text.strip():
            return 0.0
        lines = text.splitlines()
        total = len(lines)
        if total == 0:
            return 0.0

        headings = sum(1 for l in lines if l.strip().startswith("#"))
        lists = sum(1 for l in lines if re.match(r"^\s*[-*\d]+[.)]\s", l))
        blank = sum(1 for l in lines if not l.strip())

        # Score: having headings, lists, and paragraph breaks is good
        heading_ratio = min(headings / max(total, 1) * 20, 0.3)
        list_ratio = min(lists / max(total, 1) * 10, 0.3)
        paragraph_ratio = min(blank / max(total, 1) * 5, 0.2)

        return min(1.0, 0.2 + heading_ratio + list_ratio + paragraph_ratio)
