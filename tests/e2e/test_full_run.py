"""End-to-end tests for AutoImprove.

Tests the full system: preflight → grounding → loop → reporting.
Uses a mock agent so no API keys are needed.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

# Paths
E2E_DIR = Path(__file__).parent
SAMPLE_PROJECT = E2E_DIR / "sample_project"
MOCK_AGENT = str(E2E_DIR / "mock_agent.py")
REPO_ROOT = E2E_DIR.parent.parent


@pytest.fixture
def sample_repo(tmp_path):
    """Create a git-initialized copy of the sample project in a temp dir."""
    project_dir = tmp_path / "project"
    shutil.copytree(SAMPLE_PROJECT, project_dir)

    # Init git repo
    _run(["git", "init"], cwd=project_dir)
    _run(["git", "config", "user.email", "test@test.com"], cwd=project_dir)
    _run(["git", "config", "user.name", "Test"], cwd=project_dir)
    _run(["git", "add", "-A"], cwd=project_dir)
    _run(["git", "commit", "-m", "initial"], cwd=project_dir)

    # Create config.yaml
    config = {
        "agent_command": f"{sys.executable} {MOCK_AGENT}",
        "agent_timeout_seconds": 30,
        "time_budget_minutes": 2,
        "max_iterations": 3,
        "max_consecutive_rejections": 5,
        "max_file_churn": 5,
        "min_confidence_threshold": 0.0,
        "eval_refinement_interval": 100,  # disable for short tests
        "llm_judge_model": "mock",
        "llm_judge_runs": 1,
        "grounding_mode": "auto",
        "orchestration_mode": "single",
        "target_paths": ["."],
        "exclude_paths": ["tests/", ".autoimprove/"],
        "protected_paths": ["*.lock"],
        "max_diff_lines": 500,
        "allow_dependency_changes": False,
        "secret_patterns": ["AKIA[0-9A-Z]{16}"],
        "confidence_thresholds": {"code": 0.0, "workflow": 0.0, "document": 0.0},
    }
    (project_dir / "config.yaml").write_text(
        __import__("yaml").dump(config, default_flow_style=False)
    )

    # Create program.md
    (project_dir / "program.md").write_text(
        "# Test Project\n\n## Goals\n- Fix lint issues\n- Improve error handling\n"
    )

    # Commit config
    _run(["git", "add", "-A"], cwd=project_dir)
    _run(["git", "commit", "-m", "add config"], cwd=project_dir)

    return project_dir


def _run(cmd, cwd=None, check=True):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=check, timeout=60)


# ======================================================================
# Test 1: Preflight passes on valid repo
# ======================================================================


class TestPreflight:
    def test_preflight_passes_on_valid_repo(self, sample_repo):
        """Preflight should pass on a clean git repo with valid config."""
        from src.config import load_config
        from src.preflight import run_preflight

        config = load_config(str(sample_repo / "config.yaml"))
        result = run_preflight(config, str(sample_repo))

        assert result.passed, f"Preflight failed: {result.errors}"

    def test_preflight_fails_on_non_git_dir(self, tmp_path):
        """Preflight should fail when run outside a git repo."""
        from src.config import Config
        from src.preflight import run_preflight

        config = Config(time_budget_minutes=5, target_paths=["."])
        result = run_preflight(config, str(tmp_path))

        assert not result.passed
        assert any("git" in e.lower() for e in result.errors)


# ======================================================================
# Test 2: Config loading
# ======================================================================


class TestConfig:
    def test_config_loads(self, sample_repo):
        from src.config import load_config

        config = load_config(str(sample_repo / "config.yaml"))
        assert config.time_budget_minutes == 2
        assert config.max_iterations == 3

    def test_config_validation_warnings(self):
        from src.config import Config, validate_config

        config = Config(time_budget_minutes=1, target_paths=["src/"])
        warnings = validate_config(config)
        assert any("short" in w.lower() for w in warnings)


# ======================================================================
# Test 3: Run context lifecycle
# ======================================================================


class TestRunContext:
    def test_run_context_lifecycle(self, sample_repo):
        from src.config import load_config
        from src.run_context import RunContext

        config = load_config(str(sample_repo / "config.yaml"))
        ctx = RunContext(config, str(sample_repo))
        ctx.initialize()

        assert ctx.run_id
        assert ctx.worktree_path.exists()
        assert ctx.run_dir.exists()
        assert (ctx.run_dir / "config.yaml").exists()

        # Cleanup
        ctx.cleanup()


# ======================================================================
# Test 4: Plugin discovery
# ======================================================================


class TestPlugins:
    def test_code_plugin_discovers_targets(self, sample_repo):
        from src.plugins.code_plugin import CodePlugin

        plugin = CodePlugin()
        targets = plugin.discover_targets([str(sample_repo)], ["tests/", ".autoimprove/"])
        py_files = [t for t in targets if t.endswith(".py")]
        assert len(py_files) >= 2  # main.py, utils.py

    def test_registry_discovers_all_plugins(self):
        from src.plugins.registry import PluginRegistry

        reg = PluginRegistry()
        reg.discover_and_register_defaults()
        names = reg.list_plugins()
        assert "code" in names
        assert "workflow" in names
        assert "document" in names


# ======================================================================
# Test 5: Policy enforcement
# ======================================================================


class TestPolicy:
    def test_empty_diff_rejected(self):
        from src.config import Config
        from src.policy import check_policy
        from src.types import Diff

        config = Config(time_budget_minutes=5, target_paths=["src/"])
        diff = Diff(files_changed=[], lines_added=0, lines_removed=0, raw_diff="")
        result = check_policy(diff, config)
        assert not result.passed

    def test_protected_path_rejected(self):
        from src.config import Config
        from src.policy import check_policy
        from src.types import Diff

        config = Config(
            time_budget_minutes=5,
            target_paths=["src/"],
            protected_paths=["*.lock"],
        )
        diff = Diff(
            files_changed=["package-lock.json"],
            lines_added=1, lines_removed=0,
            raw_diff="+something",
        )
        result = check_policy(diff, config)
        # package-lock.json matches *.lock pattern
        assert not result.passed

    def test_secret_detected(self):
        from src.config import Config
        from src.policy import check_policy
        from src.types import Diff

        config = Config(time_budget_minutes=5, target_paths=["src/"])
        diff = Diff(
            files_changed=["config.py"],
            lines_added=1, lines_removed=0,
            raw_diff="+aws_key = 'AKIAIOSFODNN7EXAMPLE'",
        )
        result = check_policy(diff, config)
        assert not result.passed
        assert any("secret" in v.rule for v in result.violations)


# ======================================================================
# Test 6: Criteria management
# ======================================================================


class TestCriteria:
    def test_create_and_load(self, tmp_path):
        from src.eval.criteria import CriteriaItem, CriteriaManager

        mgr = CriteriaManager(tmp_path)
        items = CriteriaManager.default_code_criteria()
        v = mgr.create_initial(items, "code")

        assert v.version == 1
        assert len(v.items) > 0

        # Load from disk
        mgr2 = CriteriaManager.load_all(tmp_path)
        assert mgr2.get_current().version == 1

    def test_proposals_recorded(self, tmp_path):
        from src.eval.criteria import CriteriaManager

        mgr = CriteriaManager(tmp_path)
        mgr.create_initial(CriteriaManager.default_code_criteria(), "code")
        mgr.record_proposal(5, [{"action": "add", "reason": "test"}], "testing")

        proposals = mgr.get_proposals()
        assert len(proposals) == 1
        assert proposals[0].iteration == 5


# ======================================================================
# Test 7: Search memory
# ======================================================================


class TestSearchMemory:
    def test_record_and_query(self, tmp_path):
        from src.eval.search_memory import SearchMemory

        mem = SearchMemory(tmp_path / "memory.json")
        mem.record_attempt(1, "Fix error handling", ["api.py"], ["api.py"],
                           "accepted", "accepted", 0.7, 0.8)
        mem.record_attempt(2, "Fix error handling", ["api.py"], ["api.py"],
                           "rejected_gate", "tests failed", None, None)

        assert len(mem.hypotheses) == 2
        assert mem.is_similar_to_previous("Fix error handling")
        assert not mem.is_similar_to_previous("Completely different task about logging")

        summary = mem.get_summary_for_prompt()
        assert "Iter 1" in summary
        assert "Iter 2" in summary

    def test_persistence(self, tmp_path):
        from src.eval.search_memory import SearchMemory

        mem = SearchMemory(tmp_path / "memory.json")
        mem.record_attempt(1, "test", [], [], "accepted", "ok", 0.5, 0.5)

        mem2 = SearchMemory.load(tmp_path / "memory.json")
        assert len(mem2.hypotheses) == 1


# ======================================================================
# Test 8: Acceptance engine
# ======================================================================


class TestAcceptanceEngine:
    def test_rejects_empty_diff(self):
        from src.config import Config
        from src.eval.engine import AcceptanceEngine, REASON_POLICY_VIOLATION
        from src.eval.llm_judge import LLMJudge
        from src.plugins.code_plugin import CodePlugin
        from src.types import Diff

        config = Config(time_budget_minutes=5, target_paths=["src/"])
        engine = AcceptanceEngine(config, CodePlugin(), LLMJudge(config))

        diff = Diff(files_changed=[], lines_added=0, lines_removed=0, raw_diff="")
        decision = engine.evaluate(diff, [], 0.5, {}, 1)

        assert decision.decision.value == "rejected"
        assert decision.reason == REASON_POLICY_VIOLATION


# ======================================================================
# Test 9: Git operations
# ======================================================================


class TestGitOps:
    def test_worktree_lifecycle(self, sample_repo):
        from src import git_ops

        wt_path = str(sample_repo / "test_worktree")
        git_ops.create_worktree(str(sample_repo), wt_path, "test-branch")
        assert Path(wt_path).exists()

        git_ops.remove_worktree(str(sample_repo), wt_path)

    def test_run_id_format(self):
        from src import git_ops

        rid = git_ops.generate_run_id()
        parts = rid.split("-")
        assert len(parts) == 3
        assert len(parts[0]) == 8  # YYYYMMDD
        assert len(parts[1]) == 6  # HHMMSS
        assert len(parts[2]) == 6  # hex hash


# ======================================================================
# Test 10: Experiment log
# ======================================================================


class TestExperimentLog:
    def test_append_and_stats(self, tmp_path):
        from src.reporting.experiment_log import ExperimentEntry, ExperimentLog

        log = ExperimentLog(tmp_path / "log.json")
        log.append(ExperimentEntry(
            iteration=0, timestamp="t", hypothesis="test",
            files_modified=["a.py"], diff_lines_added=5, diff_lines_removed=2,
            diff_snippet="", decision="accepted", reason="accepted",
            reason_detail="", composite_score=0.7, confidence=0.8,
        ))
        log.append(ExperimentEntry(
            iteration=1, timestamp="t", hypothesis="test2",
            files_modified=["b.py"], diff_lines_added=3, diff_lines_removed=1,
            diff_snippet="", decision="rejected", reason="no_improvement",
            reason_detail="", composite_score=0.4, confidence=0.6,
        ))

        stats = log.get_stats()
        assert stats["total"] == 2
        assert stats["accepted"] == 1
        assert stats["rejected"] == 1

        # Persistence
        log2 = ExperimentLog.load(tmp_path / "log.json")
        assert len(log2.get_all()) == 2


# ======================================================================
# Test 11: Full integration — run with mock agent
# ======================================================================


class TestFullRun:
    def test_full_run_auto_mode(self, sample_repo):
        """Run AutoImprove end-to-end with mock agent in auto mode."""
        from src.config import load_config
        from src.orchestrator import run_autoimprove

        config = load_config(str(sample_repo / "config.yaml"))

        # Run from the sample repo directory
        original_cwd = os.getcwd()
        try:
            os.chdir(sample_repo)
            run_autoimprove(config)
        except SystemExit:
            pass  # Expected on some stop conditions
        finally:
            os.chdir(original_cwd)

        # Verify artifacts created
        ai_dir = sample_repo / ".autoimprove"
        assert ai_dir.exists(), ".autoimprove directory should exist"

        runs_dir = ai_dir / "runs"
        assert runs_dir.exists(), "runs directory should exist"

        # Find the run directory
        run_dirs = list(runs_dir.iterdir())
        assert len(run_dirs) >= 1, "At least one run directory should exist"

        run_dir = run_dirs[0]

        # Check state file
        state_file = run_dir / "accepted_state.json"
        assert state_file.exists(), "State file should exist"
        state = json.loads(state_file.read_text())
        assert "run_id" in state
        assert "status" in state
        assert state["current_iteration"] >= 0

        # Check criteria
        criteria_dir = run_dir / "criteria"
        assert criteria_dir.exists()
        criteria_files = list(criteria_dir.glob("criteria_v*.json"))
        assert len(criteria_files) >= 1, "At least one criteria version should exist"

        # Check config frozen
        assert (run_dir / "config.yaml").exists()

    def test_status_command_after_run(self, sample_repo):
        """Status command should list runs after a run completes."""
        from src.config import load_config
        from src.orchestrator import run_autoimprove

        config = load_config(str(sample_repo / "config.yaml"))

        original_cwd = os.getcwd()
        try:
            os.chdir(sample_repo)
            run_autoimprove(config)
        except SystemExit:
            pass
        finally:
            os.chdir(original_cwd)

        # Run status command
        result = subprocess.run(
            [sys.executable, "-m", "src.cli", "status"],
            cwd=sample_repo,
            capture_output=True, text=True,
            env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
        )
        # Should show at least one run
        assert "Run ID" in result.stdout or result.returncode == 0
