"""Base agent — shared invocation logic for all sub-agents."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass, field

from src.config import Config


@dataclass
class AgentResult:
    success: bool
    output: str
    error: str | None = None
    duration_seconds: float = 0.0


class BaseAgent:
    """Invokes an LLM agent via CLI with a role-specific prompt."""

    def __init__(self, config: Config, role: str) -> None:
        self.config = config
        self.role = role
        self.agent_command = config.agent_command
        self.timeout = config.agent_timeout_seconds

    def invoke(self, prompt: str, working_dir: str) -> AgentResult:
        """Send prompt to agent CLI and capture output."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as tmp:
            tmp.write(prompt)
            prompt_file = tmp.name

        try:
            cmd, use_shell = self._build_command(prompt_file, working_dir)
            start = time.monotonic()
            result = subprocess.run(
                cmd, cwd=working_dir, capture_output=True, text=True,
                timeout=self.timeout, shell=use_shell,
            )
            duration = time.monotonic() - start
        except subprocess.TimeoutExpired:
            return AgentResult(False, "", f"Timed out after {self.timeout}s", time.monotonic() - start)
        except FileNotFoundError:
            return AgentResult(False, "", f"Command not found: {self.agent_command}", 0.0)
        finally:
            os.unlink(prompt_file)

        if result.returncode != 0:
            return AgentResult(False, result.stdout, result.stderr or f"Exit code {result.returncode}", duration)

        return AgentResult(True, result.stdout, duration_seconds=duration)

    def parse_json(self, output: str) -> dict | list | None:
        """Extract JSON from agent output. Tries multiple strategies."""
        # Strategy 1: markdown fenced JSON block
        m = re.search(r"```(?:json)?\s*\n?([\s\S]*?)\n?\s*```", output)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass

        # Strategy 2: find the outermost balanced { } or [ ]
        for opener, closer in [("{", "}"), ("[", "]")]:
            start = output.find(opener)
            if start == -1:
                continue
            depth = 0
            for i in range(start, len(output)):
                if output[i] == opener:
                    depth += 1
                elif output[i] == closer:
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(output[start : i + 1])
                        except json.JSONDecodeError:
                            break

        # Strategy 3: try the entire output as JSON
        try:
            return json.loads(output.strip())
        except json.JSONDecodeError:
            pass

        return None

    def _build_command(self, prompt_file: str, working_dir: str) -> tuple:
        """Build CLI command. Returns (command, use_shell)."""
        cmd_base = self.agent_command.split()[0].lower()
        if "claude" in cmd_base:
            return f'claude --print -p "$(cat {prompt_file})" --cwd {working_dir}', True
        if "kiro" in cmd_base:
            with open(prompt_file) as f:
                prompt_text = f.read()
            # Coder needs tools; analysis roles get a "no tools" instruction
            if self.role != "coder":
                prompt_text = (
                    "IMPORTANT: Do NOT use any tools (no code, read, grep, fs_read, execute_bash, etc). "
                    "All the information you need is provided below. "
                    "Analyze the input and respond with ONLY the requested JSON output.\n\n"
                    + prompt_text
                )
            return [
                "kiro-cli", "chat",
                "--no-interactive", "--trust-all-tools", "--wrap", "never",
                prompt_text,
            ], False
        if "codex" in cmd_base:
            return ["codex", "-q", "--prompt-file", prompt_file], False
        return [self.agent_command, prompt_file], False
