"""Indexer agent — produces a semantic codebase map.

Every target file gets indexed. Large files get their own batch.
Summaries are cached per-file by git blob SHA — only changed files
are re-indexed on subsequent runs.
"""

from __future__ import annotations

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import click

from src.agents.base import BaseAgent
from src.config import Config


class IndexerAgent(BaseAgent):
    MAX_PARALLEL = 4
    MAX_BATCH_CHARS = 30000

    def __init__(self, config: Config) -> None:
        super().__init__(config, "indexer")

    def run(
        self, target_files: list[str], working_dir: str, cache_path: Path | None = None,
    ) -> dict[str, str]:
        """Index all target files. Returns ``{filepath: summary}``."""
        from rich.live import Live
        from rich.table import Table

        wd = Path(working_dir)

        # Load cache + determine which files need re-indexing
        cache = self._load_cache(cache_path) if cache_path else {}
        file_shas = self._get_file_shas(target_files, working_dir)
        to_index = []
        summaries: dict[str, str] = {}

        for f in target_files:
            sha = file_shas.get(f)
            cached = cache.get(f)
            if cached and sha and cached.get("sha") == sha:
                summaries[f] = cached["summary"]
            else:
                to_index.append(f)

        if summaries:
            click.echo(f"  ✓ {len(summaries)} files cached (unchanged), {len(to_index)} to index")

        if not to_index:
            click.echo(f"  ✓ All {len(target_files)} files up to date")
            return summaries

        batches = self._build_smart_batches(to_index, wd)

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
            done = sum(1 for _, (ic, _) in status.items() if ic in ("✓", "✗"))
            new_summaries = sum(1 for f in to_index if f in summaries)
            table.add_row("", f"\n{done}/{len(batches)} complete, {new_summaries}/{len(to_index)} indexed")
            return table

        click.echo(f"  Indexing {len(to_index)} files in {len(batches)} batches ({self.MAX_PARALLEL} parallel)")

        with Live(build_table(), refresh_per_second=2, console=None) as live:
            with ThreadPoolExecutor(max_workers=self.MAX_PARALLEL) as pool:
                futures = {}
                for batch_num, batch in enumerate(batches):
                    status[batch_num] = ("🔄", "queued")
                    futures[pool.submit(
                        self._index_batch_tracked, batch_num, batch, wd, working_dir, status
                    )] = batch_num
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

        # Save cache
        if cache_path:
            for f in to_index:
                if f in summaries:
                    cache[f] = {"sha": file_shas.get(f, ""), "summary": summaries[f]}
            self._save_cache(cache_path, cache)

        click.echo(f"  ✓ Total: {len(summaries)}/{len(target_files)} files indexed")
        return summaries

    # ------------------------------------------------------------------
    # Batching — every file gets indexed, large files get own batch
    # ------------------------------------------------------------------

    def _build_smart_batches(self, files: list[str], wd: Path) -> list[list[str]]:
        """Group files into batches. Each batch stays under MAX_BATCH_CHARS.
        Every file is included — large files get their own batch."""
        # Get actual sizes
        sized = []
        for f in files:
            try:
                size = (wd / f).stat().st_size
            except OSError:
                size = 0
            sized.append((f, size))
        sized.sort(key=lambda x: -x[1])

        batches: list[list[str]] = []
        current_batch: list[str] = []
        current_chars = 0

        for f, size in sized:
            # Per-file char budget: proportional share of batch budget
            file_chars = min(size, self.MAX_BATCH_CHARS // max(1, len(current_batch) + 1))

            if current_batch and current_chars + file_chars > self.MAX_BATCH_CHARS:
                batches.append(current_batch)
                current_batch = []
                current_chars = 0

            current_batch.append(f)
            current_chars += min(size, self.MAX_BATCH_CHARS)  # actual contribution

            # If a single file fills the batch, flush immediately
            if size >= self.MAX_BATCH_CHARS:
                batches.append(current_batch)
                current_batch = []
                current_chars = 0

        if current_batch:
            batches.append(current_batch)

        return batches

    # ------------------------------------------------------------------
    # Batch execution
    # ------------------------------------------------------------------

    def _index_batch_tracked(
        self, batch_num: int, batch: list[str], wd: Path, working_dir: str, status: dict,
    ) -> dict[str, str]:
        """Index a single batch, updating status for live display."""
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

    def _read_batch(self, files: list[str], wd: Path) -> dict[str, str]:
        """Read files for a batch. Each file gets a fair share of the char budget."""
        n = len(files)
        per_file_budget = self.MAX_BATCH_CHARS // max(n, 1)
        contents: dict[str, str] = {}
        for f in files:
            try:
                text = (wd / f).read_text(errors="ignore")
                if len(text) > per_file_budget:
                    # Keep beginning + end (most useful context)
                    half = per_file_budget // 2
                    text = text[:half] + f"\n\n... ({len(text)} chars total, middle truncated) ...\n\n" + text[-half:]
                contents[f] = text
            except OSError:
                continue
        return contents

    # ------------------------------------------------------------------
    # Prompts and parsing
    # ------------------------------------------------------------------

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
            idx = output.find(fname)
            if idx == -1:
                continue
            chunk = output[idx : idx + 500]
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

    # ------------------------------------------------------------------
    # Formatting
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Cache — per-file by git blob SHA
    # ------------------------------------------------------------------

    def _get_file_shas(self, files: list[str], working_dir: str) -> dict[str, str]:
        """Get git blob SHA for each file (detects changes)."""
        shas: dict[str, str] = {}
        try:
            result = subprocess.run(
                ["git", "ls-files", "-s"] + files,
                cwd=working_dir, capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                for line in result.stdout.strip().splitlines():
                    parts = line.split()
                    if len(parts) >= 4:
                        shas[parts[3]] = parts[1]
        except Exception:
            pass
        return shas

    def _load_cache(self, path: Path) -> dict:
        if not path.exists():
            return {}
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}

    def _save_cache(self, path: Path, cache: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(cache, f, indent=2)

    def _count_lines(self, path: Path) -> int:
        try:
            return sum(1 for _ in open(path, errors="ignore"))
        except OSError:
            return 0
