"""Repo index — generates a compact codebase map for the agent.

Produces a markdown summary of the target files: tree structure,
file sizes, and key exports/functions per file. Cached by git SHA
so it's only regenerated when the code changes.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path


def _extract_js_exports(content: str) -> list[str]:
    """Extract function/class names from JS/TS files."""
    symbols: list[str] = []
    for m in re.finditer(
        r"(?:export\s+)?(?:async\s+)?function\s+(\w+)|"
        r"(?:export\s+)?class\s+(\w+)|"
        r"module\.exports\s*=.*?(\w+)|"
        r"exports\.(\w+)\s*=",
        content,
    ):
        name = next((g for g in m.groups() if g), None)
        if name and not name.startswith("_"):
            symbols.append(name)
    return symbols[:20]


def _extract_ts_exports(content: str) -> list[str]:
    """Extract exported symbols from TypeScript/TSX files."""
    symbols: list[str] = []
    for m in re.finditer(
        r"export\s+(?:default\s+)?(?:async\s+)?function\s+(\w+)|"
        r"export\s+(?:default\s+)?class\s+(\w+)|"
        r"export\s+(?:const|let|var)\s+(\w+)|"
        r"export\s+interface\s+(\w+)|"
        r"export\s+type\s+(\w+)",
        content,
    ):
        name = next((g for g in m.groups() if g), None)
        if name:
            symbols.append(name)
    return symbols[:20]


def _extract_symbols(file_path: Path) -> list[str]:
    """Extract key symbols from a source file."""
    try:
        content = file_path.read_text(errors="ignore")
    except OSError:
        return []

    ext = file_path.suffix
    if ext in (".ts", ".tsx"):
        return _extract_ts_exports(content)
    if ext in (".js", ".jsx"):
        return _extract_js_exports(content)
    return []


def generate_repo_index(
    target_files: list[str],
    working_dir: str,
) -> str:
    """Generate a compact markdown index of the codebase."""
    wd = Path(working_dir)
    lines = ["# Codebase Index", ""]

    # Group files by directory
    by_dir: dict[str, list[Path]] = {}
    for f in target_files:
        fp = Path(f)
        parent = str(fp.parent) if str(fp.parent) != "." else "(root)"
        by_dir.setdefault(parent, []).append(fp)

    for dir_name in sorted(by_dir):
        lines.append(f"## {dir_name}/")
        for fp in sorted(by_dir[dir_name]):
            try:
                size = (wd / fp).stat().st_size
            except OSError:
                size = 0
            loc = _count_lines(wd / fp)
            symbols = _extract_symbols(wd / fp)
            sym_str = f" — {', '.join(symbols)}" if symbols else ""
            lines.append(f"- `{fp.name}` ({loc} lines){sym_str}")
        lines.append("")

    return "\n".join(lines)


def _count_lines(path: Path) -> int:
    try:
        return sum(1 for _ in open(path, errors="ignore"))
    except OSError:
        return 0


def get_or_generate_index(
    target_files: list[str],
    working_dir: str,
    index_path: Path,
) -> str:
    """Return cached index if current, otherwise regenerate."""
    meta_path = index_path.with_suffix(".meta.json")

    # Get current HEAD sha
    current_sha = None
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=working_dir, capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            current_sha = result.stdout.strip()
    except Exception:
        pass

    # Check cache
    if index_path.exists() and meta_path.exists() and current_sha:
        try:
            meta = json.loads(meta_path.read_text())
            if meta.get("sha") == current_sha:
                return index_path.read_text()
        except (json.JSONDecodeError, OSError):
            pass

    # Generate fresh index
    index = generate_repo_index(target_files, working_dir)

    # Save cache
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(index)
    if current_sha:
        meta_path.write_text(json.dumps({"sha": current_sha}))

    return index
