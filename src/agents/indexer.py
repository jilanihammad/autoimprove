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
    MAX_FILE_CHARS = 6000
    MAX_PARALLEL = 4
    MAX_BATCH_CHARS = 30000  # total chars per batch prompt

    def __init__(self, config: Config) -> None:
        super().__init__(config, "indexer")

    def run(self, target_files: list[str], working_dir: str) -> dict[str, str]:
        """Produce semantic summaries for all target files (parallel batches)."""
        from rich.live import Live
        from rich.table import Table

        wd = Path(working_dir)
        summaries: dict[str, str] = {}

        batches = self._build_smart_batches(target_files, wd)

        # Track status per batch
        status: dict[int, tuple[str, str]] = {
            i: ("⏳", "waiting") for i in range(len(batches))
        }

        def build_table() -> Table:
            table = Table(show_header=False, box=None, padding=(0, 1))
            table.add_column(width=3)
            table.add_column()
            for i in range(len(batches)):
                icon, msg = status[i]
                files_preview = ", ".join(Path(f).name for f in batches[i][:3])
                if len(batches[i]) > 3:
                    files_preview += f" +{len(batches[i]) - 3}"
                table.add_row(icon, f"Batch {i+1} ({len(batches[i])} files): {msg} [{files_preview}]")
            done = sum(1 for _, (ic, _) in status.items() if ic == "✓")
            table.add_row("", f"\n{done}/{len(batches)} complete, {len(summaries)} summaries")
            return table

        click.echo(f"  Indexing {len(target_files)} files in {len(batches)} batches ({self.MAX_PARALLEL} parallel)")

        with Live(build_table(), refresh_per_second=2, console=None) as live:
            with ThreadPoolExecutor(max_workers=self.MAX_PARALLEL) as pool:
                futures = {}
                for batch_num, batch in enumerate(batches):
                    status[batch_num] = ("🔄", "queued")
                    futures[pool.submit(self._index_batch_tracked, batch_num, batch, wd, working_dir, status)] = batch_num
                    live.update(build_table())

                for future in as_completed(futures):
                    batch_num = futures[future]
                    try:
                        batch_summaries = future.result()
                        summaries.update(batch_summaries)
                        status[batch_num] = ("✓", f"{len(batch_summaries)} summaries")
                    except Exception as e:
                        status[batch_num] = ("✗", f"error: {e}")
                    live.update(build_table())

        click.echo(f"  ✓ Total: {len(summaries)}/{len(target_files)} files indexed")
        return summaries

    def _build_smart_batches(self, files: list[str], wd: Path) -> list[list[str]]:
        """Group files into batches respecting both file count and total char limits."""
        batches: list[list[str]] = []
        current_batch: list[str] = []
        current_chars = 0

        # Sort by size so large files get their own batch
        sized = []
        for f in files:
            try:
                size = min((wd / f).stat().st_size, self.MAX_FILE_CHARS)
            except OSError:
                size = 0
            sized.append((f, size))
        sized.sort(key=lambda x: -x[1])

        for f, size in sized:
            if current_batch and (len(current_batch) >= self.BATCH_SIZE or current_chars + size > self.MAX_BATCH_CHARS):
                batches.append(current_batch)
                current_batch = []
                current_chars = 0
            current_batch.append(f)
            current_chars += size

        if current_batch:
            batches.append(current_batch)

        return batches

    def _index_batch_tracked(
        self, batch_num: int, batch: list[str], wd: Path, working_dir: str, status: dict
    ) -> dict[str, str]:
        """Index a single batch, updating status dict for live display."""
        status[batch_num] = ("🔄", "reading files...")
        file_contents = self._read_batch(batch, wd)
        if not file_contents:
            return {}

        status[batch_num] = ("🔄", "agent working...")
        prompt = self._build_prompt(file_contents)
        result = self.invoke(prompt, working_dir)

        if result.success:
            parsed = self._parse_summaries(result.output)
            if parsed:
                return parsed

        # Retry
        status[batch_num] = ("🔄", "retrying...")
        retry_prompt = prompt + "\n\nCRITICAL: Respond with ONLY valid JSON. No explanation, no markdown fences, just the raw JSON object."
        result = self.invoke(retry_prompt, working_dir)
        if result.success:
            parsed = self._parse_summaries(result.output)
            if parsed:
                return parsed

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
