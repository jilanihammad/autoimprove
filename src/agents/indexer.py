"""Indexer agent — produces a semantic codebase map.

Reads target files in batches and generates per-file summaries:
purpose, key abstractions, dependencies, complexity hotspots.
"""

from __future__ import annotations

import json
from pathlib import Path

import click

from src.agents.base import BaseAgent
from src.config import Config


class IndexerAgent(BaseAgent):
    BATCH_SIZE = 8
    MAX_FILE_CHARS = 3000

    def __init__(self, config: Config) -> None:
        super().__init__(config, "indexer")

    def run(self, target_files: list[str], working_dir: str) -> dict[str, str]:
        """Produce semantic summaries for all target files.

        Returns ``{filepath: summary_text}``.
        """
        wd = Path(working_dir)
        summaries: dict[str, str] = {}

        batches = [
            target_files[i : i + self.BATCH_SIZE]
            for i in range(0, len(target_files), self.BATCH_SIZE)
        ]

        for batch_num, batch in enumerate(batches):
            click.echo(f"  ⏳ Indexing batch {batch_num + 1}/{len(batches)} ({len(batch)} files)...", nl=False)
            file_contents = self._read_batch(batch, wd)
            if not file_contents:
                click.echo(" skipped (unreadable)")
                continue

            prompt = self._build_prompt(file_contents)
            result = self.invoke(prompt, working_dir)

            if result.success:
                parsed = self._parse_summaries(result.output)
                summaries.update(parsed)
                click.echo(f" {len(parsed)} summaries ({result.duration_seconds:.0f}s)")
            else:
                click.echo(f" failed ({result.error or 'unknown'})")

        return summaries

    def format_index(self, summaries: dict[str, str], target_files: list[str], working_dir: str) -> str:
        """Format summaries into a markdown codebase map."""
        wd = Path(working_dir)
        by_dir: dict[str, list[str]] = {}
        for f in target_files:
            parent = str(Path(f).parent) if str(Path(f).parent) != "." else "(root)"
            by_dir.setdefault(parent, []).append(f)

        lines = ["# Semantic Codebase Index", ""]
        for dir_name in sorted(by_dir):
            lines.append(f"## {dir_name}/")
            for fp in sorted(by_dir[dir_name]):
                loc = self._count_lines(wd / fp)
                summary = summaries.get(fp, "No summary available.")
                lines.append(f"### `{Path(fp).name}` ({loc} lines)")
                lines.append(summary)
                lines.append("")

        return "\n".join(lines)

    def _read_batch(self, files: list[str], wd: Path) -> dict[str, str]:
        contents: dict[str, str] = {}
        for f in files:
            try:
                text = (wd / f).read_text(errors="ignore")
                if len(text) > self.MAX_FILE_CHARS:
                    text = text[: self.MAX_FILE_CHARS] + "\n... (truncated)"
                contents[f] = text
            except OSError:
                continue
        return contents

    def _build_prompt(self, file_contents: dict[str, str]) -> str:
        files_section = "\n".join(
            f"--- {fp} ---\n{content}\n"
            for fp, content in file_contents.items()
        )
        return f"""You are a senior engineer producing a codebase index. For each file below, provide a concise summary.

For each file, output:
- **Purpose**: What this file/module does (1 sentence)
- **Key abstractions**: Main classes, functions, or patterns
- **Dependencies**: What it imports from / is used by
- **Complexity hotspots**: Anything notably complex or fragile

{files_section}

Respond ONLY with JSON (no markdown fences):
{{
  "summaries": {{
    "path/to/file.js": "Purpose: ... Key abstractions: ... Dependencies: ... Complexity: ...",
    ...
  }}
}}"""

    def _parse_summaries(self, output: str) -> dict[str, str]:
        parsed = self.parse_json(output)
        if isinstance(parsed, dict):
            return parsed.get("summaries", parsed)
        return {}

    def _count_lines(self, path: Path) -> int:
        try:
            return sum(1 for _ in open(path, errors="ignore"))
        except OSError:
            return 0
