"""Modifier agent — focused changes for a single backlog item.

Gets a minimal context: one task + only the relevant files.
No repo index, no search memory, no program.md bloat.

The modifier role, constraints, and approach are configured by the plugin,
making this agent artifact-agnostic.
"""

from __future__ import annotations

import click

from src.agents.base import BaseAgent
from src.backlog import BacklogItem
from src.config import Config


class ModifierAgent(BaseAgent):
    MAX_FILE_CHARS = 8000

    def __init__(self, config: Config) -> None:
        super().__init__(config, "modifier")

    def run(
        self,
        item: BacklogItem,
        file_contents: dict[str, str],
        eval_anchors_agent: str,
        modifier_role: str = "",
        modifier_constraints: list[str] | None = None,
    ) -> "AgentResult":
        """Make a focused code change for a single backlog item."""
        click.echo(f"│ ⏳ Modifier working on: {item.title}...", nl=False)
        prompt = self._build_prompt(item, file_contents, eval_anchors_agent, modifier_role, modifier_constraints)
        # Coder runs in modify mode — needs working_dir but we pass it via invoke
        # The working_dir is set by the orchestrator
        result = self.invoke(prompt, self._working_dir)
        click.echo(f" {result.duration_seconds:.0f}s")
        return result

    def run_in(
        self, item: BacklogItem, file_contents: dict[str, str], eval_anchors_agent: str,
        working_dir: str, modifier_role: str = "", modifier_constraints: list[str] | None = None,
    ) -> "AgentResult":
        """Make a focused code change, specifying working directory."""
        click.echo(f"│ ⏳ Modifier working on: {item.title}...", nl=False)
        prompt = self._build_prompt(item, file_contents, eval_anchors_agent, modifier_role, modifier_constraints)
        result = self.invoke(prompt, working_dir)
        click.echo(f" {result.duration_seconds:.0f}s")
        return result

    def _build_prompt(
        self, item: BacklogItem, file_contents: dict[str, str], eval_anchors: str,
        modifier_role: str = "", modifier_constraints: list[str] | None = None,
    ) -> str:
        files_section = ""
        for fp, content in file_contents.items():
            truncated = content[:self.MAX_FILE_CHARS]
            if len(content) > self.MAX_FILE_CHARS:
                truncated += "\n... (truncated)"
            files_section += f"\n--- {fp} ---\n{truncated}\n"

        retry_note = ""
        if item.attempts > 1 and item.last_rejection_reason:
            retry_note = f"""
## Previous Attempt Failed
Your last attempt at this task was rejected: {item.last_rejection_reason}
Make a different approach this time.
"""

        role = modifier_role or "You are a distinguished principal engineer."

        if modifier_constraints:
            constraints_text = "\n".join(f"{i+1}. {c}" for i, c in enumerate(modifier_constraints))
        else:
            constraints_text = (
                "1. Read the files above carefully\n"
                "2. Make the specific change described in the task\n"
                "3. Use your tools to edit the files directly — write the changes to disk\n"
                "4. State what you changed (start with \"Hypothesis:\")\n"
                "5. Do NOT modify any files not listed above\n"
                "6. Do NOT add new dependencies\n"
                "7. Keep changes minimal and focused — do not refactor beyond the task scope\n"
                "8. Verify your changes don't break the existing API contracts"
            )

        return f"""{role}

## Task
**{item.title}**
{item.description}

## Files to Modify
{files_section}

{eval_anchors}
{retry_note}
## Instructions
{constraints_text}

After making your changes, state what you changed (start with "Hypothesis:").
"""
