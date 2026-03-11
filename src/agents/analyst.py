"""Analyst agent — principal staff engineer who identifies issues and produces a backlog.

Reads the semantic index, program.md, eval anchors, and project memory,
then produces a prioritized list of specific, actionable improvements.
"""

from __future__ import annotations

import click

from src.agents.base import BaseAgent
from src.config import Config


class AnalystAgent(BaseAgent):
    def __init__(self, config: Config) -> None:
        super().__init__(config, "analyst")

    def run(
        self,
        semantic_index: str,
        program_md: str,
        eval_anchors_agent: str,
        project_memory: str,
        working_dir: str,
    ) -> list[dict]:
        """Analyze codebase and produce a prioritized improvement backlog."""
        from rich.live import Live
        from rich.spinner import Spinner

        spinner = Spinner("dots", text="Analyst agent reviewing codebase and building backlog...")

        with Live(spinner, refresh_per_second=4):
            prompt = self._build_prompt(semantic_index, program_md, eval_anchors_agent, project_memory)
            result = self.invoke(prompt, working_dir)

        if not result.success:
            click.echo(f"  ✗ Analyst failed ({result.error or 'unknown'})")
            return []

        parsed = self.parse_json(result.output)
        items = []
        if isinstance(parsed, dict):
            items = parsed.get("backlog", [])
        elif isinstance(parsed, list):
            items = parsed

        click.echo(f"  ✓ {len(items)} issues identified ({result.duration_seconds:.0f}s)")
        return items

    def _build_prompt(
        self, semantic_index: str, program_md: str, eval_anchors: str, project_memory: str,
    ) -> str:
        memory_section = f"\n## Previous Run History\n{project_memory}\n" if project_memory else ""
        return f"""You are a principal staff engineer conducting a code review. Your job is to produce a prioritized backlog of specific, actionable improvements.

## Project Context & Goals
{program_md}

{eval_anchors}

## Codebase Map
{semantic_index}
{memory_section}
## Your Task
Analyze the codebase and produce a backlog of 10-20 improvements, ordered by impact. For each item:
- **title**: Short name (e.g., "Add input validation to /api/chat")
- **description**: Exactly what to change and why. Be specific — reference function names, line patterns, concrete issues.
- **files**: List of files that need to be modified (use exact paths from the codebase map)
- **priority**: 0.0 to 1.0 (1.0 = highest impact, most urgent)
- **category**: One of: error_handling, complexity, type_safety, performance, readability, maintainability, validation, documentation

Rules:
- Each item must be a single focused change (not a sweeping rewrite)
- Reference specific functions/classes from the codebase map
- Do NOT include items that conflict with the must-preserve constraints
- Prioritize items that the project owner explicitly asked for in the improvement goals
- If previous runs tried and failed certain changes, do NOT re-propose them

Respond ONLY with JSON (no markdown fences):
{{
  "backlog": [
    {{
      "title": "...",
      "description": "...",
      "files": ["path/to/file.js"],
      "priority": 0.9,
      "category": "error_handling"
    }},
    ...
  ]
}}"""
