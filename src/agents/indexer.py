"""Indexer agent — produces a semantic codebase map.

Reads target files in batches and generates per-file summaries:
purpose, key abstractions, dependencies, complexity hotspots.
Batches run in parallel for speed.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import click

from src.agents.base import BaseAgent
from src.config import Config


class IndexerAgent(BaseAgent):
    BATCH_SIZE = 8
    MAX_FILE_CHARS = 3000
    MAX_PARALLEL = 4

    def __init__(self, config: Config) -> None:
        super().__init__(config, "indexer")

    def run(self, target_files: list[str], working_dir: str) -> dict[str, str]:
        """Produce semantic summaries for all target files (parallel batches)."""
        wd = Path(working_dir)
        summaries: dict[str, str] = {}

        batches = [
            target_files[i : i + self.BATCH_SIZE]
            for i in range(0, len(target_files), self.BATCH_SIZE)
        ]

        click.echo(f"  Indexing {len(target_files)} files in {len(batches)} batches ({self.MAX_PARALLEL} parallel)...")

        with ThreadPoolExecutor(max_workers=self.MAX_PARALLEL) as pool:
            futures = {
                pool.submit(self._index_batch, batch, wd, working_dir): batch_num
                for batch_num, batch in enumerate(batches)
            }

            for future in as_completed(futures):
                batch_num = futures[future]
                try:
                    batch_summaries = future.result()
                    summaries.update(batch_summaries)
                    click.echo(f"  ✓ Batch {batch_num + 1}/{len(batches)}: {len(batch_summaries)} summaries")
                except Exception as e:
                    click.echo(f"  ✗ Batch {batch_num + 1}/{len(batches)}: error ({e})")

        click.echo(f"  ✓ Total: {len(summaries)}/{len(target_files)} files indexed")
        return summaries

    def _index_batch(self, batch: list[str], wd: Path, working_dir: str) -> dict[str, str]:
        """Index a single batch. Called from thread pool."""
        file_contents = self._read_batch(batch, wd)
        if not file_contents:
            return {}

        prompt = self._build_prompt(file_contents)
        result = self.invoke(prompt, working_dir)

        if result.success:
            parsed = self._parse_summaries(result.output)
            if parsed:
                return parsed

        # Retry with stricter prompt
        retry_prompt = prompt + "\n\nCRITICAL: Respond with ONLY valid JSON. No explanation, no markdown fences, just the raw JSON object."
        result = self.invoke(retry_prompt, working_dir)
        if result.success:
            parsed = self._parse_summaries(result.output)
            if parsed:
                return parsed

        # Fallback
        return self._fallback_parse(
            result.output if result.success else "", list(file_contents.keys())
        )

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

    def _fallback_parse(self, output: str, file_paths: list[str]) -> dict[str, str]:
        """Extract per-file summaries from unstructured text output."""
        summaries: dict[str, str] = {}
        for fp in file_paths:
            fname = Path(fp).name
            # Look for the filename in the output and grab the next few lines
            idx = output.find(fname)
            if idx == -1:
                continue
            # Grab up to 500 chars after the filename
            chunk = output[idx : idx + 500]
            # Take lines until we hit another filename or end
            lines = []
            for line in chunk.splitlines()[1:]:
                if any(Path(other).name in line for other in file_paths if other != fp):
                    break
                stripped = line.strip()
                if stripped:
                    lines.append(stripped)
                if len(lines) >= 4:
                    break
            if lines:
                summaries[fp] = " ".join(lines)
        return summaries

    def _count_lines(self, path: Path) -> int:
        try:
            return sum(1 for _ in open(path, errors="ignore"))
        except OSError:
            return 0
