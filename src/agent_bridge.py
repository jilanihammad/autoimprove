"""Agent bridge — agent-agnostic interface for invoking CLI coding agents.

Handles prompt construction, invocation, response capture, and timeouts.
The rest of the system never calls an agent directly — always through this bridge.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from src import git_ops
from src.config import Config


@dataclass
class AgentRequest:
    prompt: str
    working_dir: str
    timeout_seconds: int
    context_files: list[str] = field(default_factory=list)
    mode: str = "modify"  # "modify" or "analyze"


@dataclass
class AgentResponse:
    success: bool
    output: str
    error: str | None = None
    duration_seconds: float = 0.0
    files_modified: list[str] = field(default_factory=list)


class AgentBridge:
    """Invokes any CLI-based coding agent (Claude, Codex, Kiro, custom)."""

    def __init__(self, config: Config) -> None:
        self.agent_command = config.agent_command
        self.timeout = config.agent_timeout_seconds

    def invoke(self, request: AgentRequest) -> AgentResponse:
        """Send a prompt to the agent and capture the result."""
        agent_type = self._detect_agent_type()

        # Snapshot git state before invocation to detect changes
        try:
            before_sha = git_ops.get_head_sha(request.working_dir, short=False)
        except git_ops.GitError:
            before_sha = None

        # Write prompt to temp file to avoid shell arg length limits
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as tmp:
            tmp.write(request.prompt)
            prompt_file = tmp.name

        try:
            cmd = self._build_command(agent_type, prompt_file, request)
            stdout, stderr, rc, duration = self._invoke_subprocess(
                cmd, request.working_dir, request.timeout_seconds
            )
        finally:
            os.unlink(prompt_file)

        if rc != 0:
            return AgentResponse(
                success=False,
                output=stdout,
                error=stderr or f"Agent exited with code {rc}",
                duration_seconds=duration,
            )

        # Detect modified files via git
        files_modified: list[str] = []
        if before_sha and request.mode == "modify":
            try:
                result = subprocess.run(
                    ["git", "diff", "--name-only", before_sha],
                    cwd=request.working_dir,
                    capture_output=True, text=True, timeout=30,
                )
                files_modified = [f for f in result.stdout.strip().splitlines() if f]
            except Exception:
                pass

        return AgentResponse(
            success=True,
            output=stdout,
            duration_seconds=duration,
            files_modified=files_modified,
        )

    # ------------------------------------------------------------------
    # Prompt builders
    # ------------------------------------------------------------------

    def build_improvement_prompt(
        self,
        program_md: str,
        search_memory_summary: str,
        iteration: int,
        criteria_summary: str,
        previous_outcomes: list[str],
        project_memory: str = "",
        eval_anchors: str = "",
        repo_index: str = "",
    ) -> str:
        outcomes_text = "\n".join(previous_outcomes[-5:]) if previous_outcomes else "None yet."
        memory_section = f"\n## Project Memory (from previous runs)\n{project_memory}\n" if project_memory else ""
        anchors_section = f"\n{eval_anchors}\n" if eval_anchors else ""
        index_section = f"\n## Codebase Map\n{repo_index}\n" if repo_index else ""
        return f"""# AutoImprove — Iteration {iteration}

## Your Instructions
{program_md}
{anchors_section}{index_section}
## Evaluation Criteria
Your changes will be evaluated against these criteria:
{criteria_summary}
{memory_section}
## Previous Attempts (this run)
{search_memory_summary}

## Recent Outcomes
{outcomes_text}

## Your Task
1. Review the codebase in your working directory
2. Identify the single highest-impact improvement you can make
3. State your hypothesis: what you'll change and why (start with "Hypothesis:")
4. Make the changes
5. Verify your changes don't break anything obvious

## Constraints
- Make focused, bounded changes (not sweeping rewrites)
- Do not modify files outside the target paths
- Do not add new dependencies unless absolutely necessary
- Do not retry improvements that were already rejected (see previous attempts and project memory)
- Your changes will be automatically evaluated — focus on quality over quantity
"""

    def build_grounding_prompt(
        self, program_md: str, profile_md: str, artifact_summary: str
    ) -> str:
        return f"""# AutoImprove — Grounding Phase

## Project Context
{program_md}

## Evaluation Profile
{profile_md}

## Current Artifacts
{artifact_summary}

## Your Task
Analyze the project and propose:
1. **Evaluation Criteria**: A rubric for judging improvements. For each criterion:
   - name: short identifier
   - description: what it measures
   - weight: 0.0-1.0 (must sum to 1.0 for scored items)
   - is_hard_gate: true for pass/fail gates
   - metric_type: "deterministic" or "judgment"
2. **Improvement Hypotheses**: Ranked list of potential improvements, each with:
   - description
   - expected_impact: high/medium/low
   - files_affected: list of files
   - risk: low/medium/high

Respond in JSON format:
{{
  "criteria": [...],
  "hypotheses": [...]
}}
"""

    def build_criteria_review_prompt(
        self, current_criteria: str, experiment_history: str, iteration: int
    ) -> str:
        return f"""# AutoImprove — Criteria Review (Iteration {iteration})

## Current Criteria
{current_criteria}

## Experiment History
{experiment_history}

## Your Task
Based on the experiment history, propose changes to the evaluation criteria.
For each proposed change, explain:
- What to change (add/remove/modify a criterion)
- Why (based on observed patterns)
- Expected impact

Respond in JSON:
{{
  "changes": [
    {{"action": "add|remove|modify", "item": {{...}}, "reason": "..."}}
  ],
  "rationale": "Overall rationale for these changes"
}}
"""

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _detect_agent_type(self) -> str:
        cmd = self.agent_command.split()[0].lower()
        if "claude" in cmd:
            return "claude"
        if "codex" in cmd:
            return "codex"
        if "kiro" in cmd:
            return "kiro"
        return "generic"

    def _build_command(
        self, agent_type: str, prompt_file: str, request: AgentRequest
    ) -> list[str]:
        if agent_type == "claude":
            return [
                "claude", "--print",
                "-p", f"$(cat {prompt_file})",
                "--cwd", request.working_dir,
            ]
        if agent_type == "codex":
            return ["codex", "-q", "--prompt-file", prompt_file]
        if agent_type == "kiro":
            # kiro-cli takes prompt as positional arg; read file content
            with open(prompt_file) as f:
                prompt_text = f.read()
            return [
                "kiro-cli", "chat",
                "--no-interactive", "--trust-all-tools",
                "--wrap", "never",
                prompt_text,
            ]
        # Generic: read prompt from file
        return [self.agent_command, prompt_file]

    def _invoke_subprocess(
        self,
        command: list[str],
        cwd: str,
        timeout: int,
        input_text: str | None = None,
    ) -> tuple[str, str, int, float]:
        """Run subprocess. Returns (stdout, stderr, returncode, duration_seconds)."""
        start = time.monotonic()
        try:
            # For claude, use shell=True to handle $() expansion
            agent_type = self._detect_agent_type()
            if agent_type == "claude":
                result = subprocess.run(
                    " ".join(command),
                    cwd=cwd,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    input=input_text,
                    shell=True,
                )
            else:
                result = subprocess.run(
                    command,
                    cwd=cwd,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    input=input_text,
                )
            duration = time.monotonic() - start
            return result.stdout, result.stderr, result.returncode, duration
        except subprocess.TimeoutExpired:
            duration = time.monotonic() - start
            return "", f"Agent timed out after {timeout}s", -1, duration
        except FileNotFoundError:
            duration = time.monotonic() - start
            return "", f"Agent command not found: {command[0]}", -1, duration
