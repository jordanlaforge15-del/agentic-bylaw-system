"""Tests for the Night Manager orchestrator.

These test the NM's core logic (config, state, planner, Linear client)
without spawning actual Claude Code agents or requiring a live Linear API.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from scripts.night_manager.config import NMConfig, parse_args
from scripts.night_manager.state import (
    ExecutionGroup,
    IssuePorts,
    IssueState,
    NMState,
)
from scripts.night_manager.planner import allocate_ports, _slugify
from scripts.night_manager.linear_client import LinearIssue


class TestConfig:
    def test_parse_defaults(self):
        with patch.dict(os.environ, {"LINEAR_API_KEY": "test-key"}):
            cfg = parse_args(["--dry-run"])
        assert cfg.dry_run is True
        assert cfg.max_agents == 3
        assert cfg.label == "Triaged"
        assert cfg.model == "opus"
        assert cfg.deploy is False
        assert cfg.issue is None

    def test_parse_custom(self):
        with patch.dict(os.environ, {"LINEAR_API_KEY": "test-key"}):
            cfg = parse_args([
                "--max-agents", "5",
                "--label", "Ready",
                "--model", "sonnet",
                "--deploy",
                "--issue", "ABS-90",
            ])
        assert cfg.max_agents == 5
        assert cfg.label == "Ready"
        assert cfg.model == "sonnet"
        assert cfg.deploy is True
        assert cfg.issue == "ABS-90"

    def test_missing_api_key_raises(self, monkeypatch):
        monkeypatch.delenv("LINEAR_API_KEY", raising=False)
        monkeypatch.setattr(
            "scripts.night_manager.config.REPO_ROOT", Path("/nonexistent")
        )
        with pytest.raises(RuntimeError, match="LINEAR_API_KEY"):
            NMConfig(linear_api_key="")


class TestState:
    def test_new_run(self):
        state = NMState.new_run({"max_agents": 3, "label": "Triaged"})
        assert state.run_id.startswith("nm-")
        assert state.config["max_agents"] == 3

    def test_save_and_load(self, tmp_path: Path):
        state = NMState.new_run({"max_agents": 2})
        state.issues["ABS-90"] = IssueState(
            identifier="ABS-90",
            title="Test Issue",
            branch="agent/ABS-90-test",
            ports=IssuePorts(pg=5433, api=8002, web=3002),
            linear_id="uuid-123",
        )
        state.plan = [ExecutionGroup(parallel=["ABS-90"])]

        state_file = tmp_path / "state.json"
        state.save(state_file)
        assert state_file.exists()

        loaded = NMState.load(state_file)
        assert loaded.run_id == state.run_id
        assert "ABS-90" in loaded.issues
        assert loaded.issues["ABS-90"].title == "Test Issue"
        assert loaded.issues["ABS-90"].ports.pg == 5433
        assert len(loaded.plan) == 1
        assert loaded.plan[0].parallel == ["ABS-90"]

    def test_issue_state_transitions(self):
        issue = IssueState(identifier="ABS-90", title="Test")
        assert issue.status == "queued"

        issue.mark_started()
        assert issue.status == "in_progress"
        assert issue.started_at is not None
        assert issue.attempts == 1

        issue.mark_completed()
        assert issue.status == "reviewing"

        issue.mark_merged()
        assert issue.status == "merged"
        assert issue.merged_at is not None

    def test_issue_state_failure(self):
        issue = IssueState(identifier="ABS-90", title="Test")
        issue.mark_failed("Something broke")
        assert issue.status == "failed"
        assert issue.error == "Something broke"

    def test_issue_state_blocked(self):
        issue = IssueState(identifier="ABS-90", title="Test")
        issue.mark_blocked("Review failed 3 times")
        assert issue.status == "blocked"
        assert issue.error == "Review failed 3 times"

    def test_active_agent_count(self):
        state = NMState.new_run({})
        state.issues["A"] = IssueState(identifier="A", title="A", status="in_progress")
        state.issues["B"] = IssueState(identifier="B", title="B", status="queued")
        state.issues["C"] = IssueState(identifier="C", title="C", status="in_progress")
        assert state.active_agent_count() == 2

    def test_pending_issues(self):
        state = NMState.new_run({})
        state.issues["A"] = IssueState(identifier="A", title="A", status="queued")
        state.issues["B"] = IssueState(identifier="B", title="B", status="in_progress")
        state.issues["C"] = IssueState(identifier="C", title="C", status="queued")
        assert set(state.pending_issues()) == {"A", "C"}

    def test_summary_buckets(self):
        state = NMState.new_run({})
        state.issues["A"] = IssueState(identifier="A", title="A", status="merged")
        state.issues["B"] = IssueState(identifier="B", title="B", status="failed")
        state.issues["C"] = IssueState(identifier="C", title="C", status="merged")
        summary = state.summary()
        assert len(summary["merged"]) == 2
        assert len(summary["failed"]) == 1

    def test_crash_recovery_roundtrip(self, tmp_path: Path):
        """State survives a write → simulated crash → reload."""
        state = NMState.new_run({"label": "Triaged"})
        state.issues["ABS-91"] = IssueState(
            identifier="ABS-91",
            title="Crash test",
            status="in_progress",
            session_id="sess-abc",
            pid=12345,
            ports=IssuePorts(pg=5434, api=8003, web=3003),
        )
        state_file = tmp_path / "state.json"
        state.save(state_file)

        recovered = NMState.load(state_file)
        issue = recovered.issues["ABS-91"]
        assert issue.status == "in_progress"
        assert issue.session_id == "sess-abc"
        assert issue.pid == 12345
        assert issue.ports.api == 8003


class TestPlanner:
    def test_allocate_ports(self):
        p0 = allocate_ports(0)
        assert p0.pg == 5433
        assert p0.api == 8002
        assert p0.web == 3002

        p1 = allocate_ports(1)
        assert p1.pg == 5434
        assert p1.api == 8003
        assert p1.web == 3003

    def test_slugify(self):
        assert _slugify("Fix the login flow") == "fix-the-login-flow"
        assert _slugify("ABS-90: Add e2e tests!") == "abs90-add-e2e-tests"
        assert len(_slugify("A" * 100)) <= 40

    def test_slugify_special_chars(self):
        assert _slugify("night_manager (v2)") == "nightmanager-v2"


class TestLinearIssue:
    def test_dataclass_fields(self):
        issue = LinearIssue(
            id="uuid-1",
            identifier="ABS-90",
            title="Test",
            description="A description",
            priority=2,
            sort_order=1.0,
            state_name="Backlog",
            state_id="state-1",
            labels=["Triaged"],
            estimate=3,
            team_id="team-1",
        )
        assert issue.identifier == "ABS-90"
        assert issue.labels == ["Triaged"]
        assert issue.estimate == 3
