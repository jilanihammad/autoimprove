"""Analyst agent — principal staff engineer who identifies issues and produces a backlog.

Reads the semantic index, program.md, eval anchors, and project memory,
then produces a prioritized list of specific, actionable improvements.
"""

from __future__ import annotations

from pathlib import Path

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
        completed_work: str = "",
        analyst_role: str = "",
        analyst_categories: list[dict[str, str]] | None = None,
        calibration_context: str = "",
    ) -> list[dict]:
        """Analyze codebase and produce a prioritized improvement backlog."""
        from rich.live import Live
        from rich.spinner import Spinner

        spinner = Spinner("dots", text="Analyst agent reviewing codebase and building backlog...")

        with Live(spinner, refresh_per_second=4):
            prompt = self._build_prompt(
                semantic_index, program_md, eval_anchors_agent, project_memory,
                completed_work, analyst_role, analyst_categories, calibration_context,
            )
            result = self.invoke(prompt, working_dir)

        if not result.success:
            click.echo(f"  ✗ Analyst failed ({result.error or 'unknown'})")
            return []

        items = self._extract_backlog(result.output)

        if not items:
            # Retry with stricter prompt
            click.echo(f"  ⚠ Could not parse backlog, retrying...")
            retry_prompt = prompt + "\n\nCRITICAL: Respond with ONLY valid JSON. No explanation, no markdown fences. Just the raw JSON object with a 'backlog' array."
            with Live(Spinner("dots", text="Analyst retrying..."), refresh_per_second=4):
                result = self.invoke(retry_prompt, working_dir)
            if result.success:
                items = self._extract_backlog(result.output)

        if not items:
            click.echo(f"  ✗ Analyst produced no parseable backlog after retry")
            # Save raw output for debugging
            try:
                debug_path = Path(working_dir) / ".." / "analyst_raw_output.txt"
                debug_path.resolve().write_text(result.output[:10000] if result.success else f"ERROR: {result.error}")
                click.echo(f"  ℹ Raw output saved to {debug_path.resolve()}")
            except OSError:
                pass
            return []

        # Check multi-artifact coverage: if we have prefixed categories but the analyst
        # only returned code items, do a supplementary call for missing artifact types
        if items and analyst_categories:
            prefixes = {c["name"].split(":")[0] for c in analyst_categories if ":" in c["name"]}
            if len(prefixes) > 1:
                item_prefixes = set()
                for it in items:
                    cat = it.get("category", "")
                    if ":" in cat:
                        item_prefixes.add(cat.split(":")[0])
                    else:
                        item_prefixes.add("code")  # assume untagged = code
                missing = prefixes - item_prefixes
                if missing:
                    click.echo(f"  ⚠ Analyst missed artifact types: {', '.join(sorted(missing))}. Requesting supplementary items...")
                    items = self._supplement_missing_artifacts(
                        items, missing, semantic_index, program_md, eval_anchors_agent,
                        analyst_categories, working_dir,
                    )

        click.echo(f"  ✓ {len(items)} issues identified ({result.duration_seconds:.0f}s)")
        return items

    def _supplement_missing_artifacts(
        self,
        existing_items: list[dict],
        missing_prefixes: set[str],
        semantic_index: str,
        program_md: str,
        eval_anchors: str,
        analyst_categories: list[dict[str, str]],
        working_dir: str,
    ) -> list[dict]:
        """Make a focused supplementary call to get items for missing artifact types."""
        from rich.live import Live
        from rich.spinner import Spinner

        # Build a focused prompt with only the missing artifact categories
        missing_cats = []
        for c in analyst_categories:
            name = c["name"]
            if ":" in name and name.split(":")[0] in missing_prefixes:
                missing_cats.append(c)

        if not missing_cats:
            return existing_items

        cat_detail = "\n".join(f"  - **{c['name']}**: {c.get('description', '')}" for c in missing_cats)
        cat_names = ", ".join(c["name"] for c in missing_cats)

        # Filter semantic index to show only relevant files for missing types
        prompt = f"""You are an expert reviewer focused on non-code artifacts. Your job is to propose 3-5 specific improvements for the following artifact types: {', '.join(sorted(missing_prefixes))}.

## Project Context
{program_md}

{eval_anchors}

## Full Codebase Map (look for non-code files: documents, configs, prompts, etc.)
{semantic_index}

## Your Task
Propose 3-5 improvements for these artifact types ONLY. Do NOT propose code changes.

Available categories (use EXACT names):
{cat_detail}

For each item provide:
- **title**: Short name
- **description**: Exactly what to change and why. Reference specific file paths.
- **files**: List of files to modify
- **priority**: 0.0 to 1.0
- **category**: One of: {cat_names}

Respond ONLY with JSON (no markdown fences):
{{
  "backlog": [
    {{
      "title": "...",
      "description": "...",
      "files": ["path/to/file"],
      "priority": 0.8,
      "category": "{missing_cats[0]['name']}"
    }}
  ]
}}"""

        with Live(Spinner("dots", text=f"Supplementary analyst for {', '.join(sorted(missing_prefixes))}..."), refresh_per_second=4):
            result = self.invoke(prompt, working_dir)

        if result.success:
            supplement = self._extract_backlog(result.output)
            if supplement:
                click.echo(f"  ✓ {len(supplement)} supplementary items for {', '.join(sorted(missing_prefixes))}")
                return existing_items + supplement

        click.echo(f"  ⚠ Supplementary call produced no items")
        return existing_items

    def _extract_backlog(self, output: str) -> list[dict]:
        """Try multiple strategies to extract backlog items from output."""
        parsed = self.parse_json(output)

        # Strategy 1: {"backlog": [...]}
        if isinstance(parsed, dict) and "backlog" in parsed:
            items = parsed["backlog"]
            if isinstance(items, list) and items:
                return items

        # Strategy 2: direct array [...]
        if isinstance(parsed, list) and parsed:
            return parsed

        # Strategy 3: any key containing a list of dicts with "title"
        if isinstance(parsed, dict):
            for v in parsed.values():
                if isinstance(v, list) and v and isinstance(v[0], dict) and "title" in v[0]:
                    return v

        return []

    def _build_prompt(
        self, semantic_index: str, program_md: str, eval_anchors: str, project_memory: str,
        completed_work: str = "",
        analyst_role: str = "",
        analyst_categories: list[dict[str, str]] | None = None,
        calibration_context: str = "",
    ) -> str:
        memory_section = f"\n## Previous Run History\n{project_memory}\n" if project_memory else ""
        completed_section = f"\n## Already Completed This Run (do NOT re-propose)\n{completed_work}\n" if completed_work else ""
        calibration_section = f"\n## Calibration from User Feedback\n{calibration_context}\n" if calibration_context else ""

        role = analyst_role or "a principal staff engineer conducting a code review"

        # Build category guidance from plugin or fall back to defaults
        multi_artifact_hint = ""
        if analyst_categories:
            # Detect multi-artifact categories (tagged with plugin prefix like "code:", "document:")
            prefixes = {c["name"].split(":")[0] for c in analyst_categories if ":" in c["name"]}

            if len(prefixes) > 1:
                # Multi-artifact: group categories by prefix for clarity
                cat_sections = []
                for prefix in sorted(prefixes):
                    prefix_cats = [c for c in analyst_categories if c["name"].startswith(f"{prefix}:")]
                    cat_lines = ", ".join(c["name"] for c in prefix_cats)
                    cat_detail = "\n".join(f"    - **{c['name']}**: {c.get('description', '')}" for c in prefix_cats)
                    cat_sections.append(f"  **{prefix}** artifacts: {cat_lines}\n{cat_detail}")
                category_block = "\n".join(cat_sections)
                category_instruction = f"- **category**: Use the EXACT category names below (including the prefix):\n{category_block}"
                multi_artifact_hint = (
                    f"\n**CRITICAL**: This project contains {len(prefixes)} artifact types: {', '.join(sorted(prefixes))}.\n"
                    f"You MUST propose improvements for EVERY artifact type — not just code.\n"
                    f"At minimum, include 2-3 items for EACH non-code artifact type.\n"
                    f"Use the full prefixed category name (e.g., 'document:clarity', 'agent:prompt_clarity').\n"
                )
            else:
                cat_names = ", ".join(c["name"] for c in analyst_categories)
                cat_detail = "\n".join(f"  - **{c['name']}**: {c.get('description', '')}" for c in analyst_categories)
                category_instruction = f"- **category**: One of: {cat_names}\n\nCategory descriptions:\n{cat_detail}"
        else:
            category_instruction = "- **category**: One of: error_handling, complexity, type_safety, performance, readability, maintainability, validation, documentation"

        # Build JSON example — show prefixed categories in multi-artifact mode
        if multi_artifact_hint:
            # Extract the first non-code prefix and category for a realistic example
            non_code_examples = []
            code_example = '"code:error_handling"'
            if analyst_categories:
                for c in analyst_categories:
                    name = c["name"]
                    if ":" in name and not name.startswith("code:"):
                        non_code_examples.append(f'"{name}"')
                    elif ":" in name and name.startswith("code:"):
                        code_example = f'"{name}"'
            non_code_cat = non_code_examples[0] if non_code_examples else '"document:clarity"'
            json_example = f"""{{
  "backlog": [
    {{
      "title": "Add input validation to /api/chat",
      "description": "...",
      "files": ["src/api/chat.py"],
      "priority": 0.9,
      "category": {code_example}
    }},
    {{
      "title": "Improve clarity of onboarding document",
      "description": "...",
      "files": ["docs/onboarding.md"],
      "priority": 0.8,
      "category": {non_code_cat}
    }},
    ...
  ]
}}"""
        else:
            json_example = """{{
  "backlog": [
    {{
      "title": "...",
      "description": "...",
      "files": ["path/to/file.js"],
      "priority": 0.9,
      "category": "category_name"
    }},
    ...
  ]
}}"""

        # Build distribution rule for multi-artifact
        distribution_rule = ""
        if multi_artifact_hint:
            distribution_rule = (
                "\n- **MANDATORY DISTRIBUTION**: Your backlog MUST include items for EVERY artifact type listed above. "
                "Allocate at least 2-3 items per non-code artifact type. A backlog with ONLY code items will be REJECTED.\n"
            )

        return f"""You are {role}. Your job is to produce a prioritized backlog of specific, actionable improvements.

## Project Context & Goals
{program_md}

{eval_anchors}

## Codebase Map
{semantic_index}
{memory_section}{completed_section}{calibration_section}
## Your Task
Analyze the project and produce a backlog of 10-20 improvements, ordered by impact. For each item:
- **title**: Short name (e.g., "Add input validation to /api/chat")
- **description**: Exactly what to change and why. Be specific — reference exact file paths, function names, or content from the codebase map.
- **files**: List of files that need to be modified (use exact paths from the codebase map)
- **priority**: 0.0 to 1.0 (1.0 = highest impact, most urgent)
{category_instruction}

Rules:
- Each item must be a single focused change (not a sweeping rewrite)
- Reference specific files and content from the codebase map
- Do NOT include items that conflict with the must-preserve constraints
- Prioritize items that the project owner explicitly asked for in the improvement goals
- If previous runs tried and failed certain changes, do NOT re-propose them
- Use ONLY the category names listed above — do not invent new categories
{distribution_rule}{multi_artifact_hint}
Respond ONLY with JSON (no markdown fences):
{json_example}"""
