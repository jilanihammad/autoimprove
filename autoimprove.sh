#!/usr/bin/env bash
set -euo pipefail

if command -v uv &>/dev/null; then
    exec uv run python -m src.cli "$@"
elif command -v python3 &>/dev/null; then
    exec python3 -m src.cli "$@"
else
    echo "Error: neither uv nor python3 found in PATH" >&2
    exit 1
fi
