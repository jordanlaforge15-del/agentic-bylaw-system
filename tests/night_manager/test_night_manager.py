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
from scripts.night_manager.planner import (
    IssueSettings,
    _apply_replan_assignments,
    _is_group_pending,
    _parse_touch_profile,
    allocate_ports,
    _slugify,
)
from scripts.night_manager.linear_client import LinearIssue


class TestConfig:
    def test_parse_defaults(self):
        with patch.dict(os.environ, {"LINEAR_API_KEY": "test-key"}):
            cfg = parse_args(["--dry-run"])
        assert cfg.dry_run is True
        assert cfg.max_agents == 2
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


class TestTouchProfileSerialization:
    def test_touch_profile_roundtrips(self, tmp_path: Path):
        """touch_profile + added_via_rescan survive save → load."""
        state = NMState.new_run({})
        state.issues["ABS-90"] = IssueState(
            identifier="ABS-90",
            title="Profile",
            ports=IssuePorts(pg=5433, api=8002, web=3002),
            touch_profile={"areas": ["scripts/x"], "kind": "backend", "risk_tags": ["auth"]},
            added_via_rescan=True,
        )
        target = tmp_path / "state.json"
        state.save(target)
        recovered = NMState.load(target)
        issue = recovered.issues["ABS-90"]
        assert issue.touch_profile == {
            "areas": ["scripts/x"],
            "kind": "backend",
            "risk_tags": ["auth"],
        }
        assert issue.added_via_rescan is True

    def test_load_older_state_without_touch_profile(self, tmp_path: Path):
        """Old state files that predate touch_profile still load."""
        # An old state.json that lacks both new fields.
        legacy = {
            "run_id": "nm-legacy",
            "started_at": "2024-01-01T00:00:00Z",
            "config": {},
            "plan": [{"parallel": ["ABS-1"], "deploy": False}],
            "issues": {
                "ABS-1": {
                    "identifier": "ABS-1",
                    "title": "Legacy issue",
                    "status": "queued",
                    "branch": "agent/ABS-1-legacy",
                    "worktree": "",
                    "ports": {"pg": 5433, "api": 8002, "web": 3002},
                    "session_id": "",
                    "pid": 0,
                    "log_file": "",
                    "attempts": 0,
                    "review_attempts": 0,
                    "started_at": None,
                    "completed_at": None,
                    "merged_at": None,
                    "error": None,
                    "rate_limited": False,
                    "agent_model": "",
                    "agent_effort": "",
                    "linear_id": "",
                },
            },
        }
        target = tmp_path / "state.json"
        target.write_text(json.dumps(legacy))
        state = NMState.load(target)
        assert state.issues["ABS-1"].touch_profile is None
        assert state.issues["ABS-1"].added_via_rescan is False


class TestParseTouchProfile:
    def test_well_formed(self):
        assert _parse_touch_profile(
            {"areas": ["a/b"], "kind": "backend", "risk_tags": ["auth"]}
        ) == {"areas": ["a/b"], "kind": "backend", "risk_tags": ["auth"]}

    def test_none_passes_through(self):
        assert _parse_touch_profile(None) is None

    def test_non_dict_returns_none(self):
        assert _parse_touch_profile("nope") is None
        assert _parse_touch_profile(["a/b"]) is None

    def test_missing_keys_get_defaults(self):
        result = _parse_touch_profile({})
        assert result == {"areas": [], "kind": "unknown", "risk_tags": []}

    def test_bad_inner_types_coerce(self):
        result = _parse_touch_profile(
            {"areas": "string-not-list", "kind": 7, "risk_tags": None}
        )
        # Strings/wrong types collapse safely, kind is stringified.
        assert result["areas"] == []
        assert result["kind"] == "7"
        assert result["risk_tags"] == []

    def test_caps_long_lists(self):
        long = [f"area-{i}" for i in range(20)]
        result = _parse_touch_profile({"areas": long, "kind": "x", "risk_tags": long})
        assert len(result["areas"]) == 8
        assert len(result["risk_tags"]) == 8


def _mk_state_with_plan(plan_groups: list[ExecutionGroup], issues_status: dict[str, str]) -> NMState:
    """Helper to build a state with a pre-existing plan + statuses."""
    state = NMState.new_run({})
    state.plan = plan_groups
    slot_iter = iter(range(100))
    for group in plan_groups:
        for ident in group.parallel:
            state.issues[ident] = IssueState(
                identifier=ident,
                title=f"Existing {ident}",
                status=issues_status.get(ident, "queued"),
                ports=IssuePorts(pg=5433 + next(slot_iter), api=8002, web=3002),
                touch_profile={"areas": [f"area/{ident}"], "kind": "backend", "risk_tags": []},
                linear_id=f"linear-{ident}",
            )
    return state


def _mk_new_linear_issue(ident: str) -> LinearIssue:
    return LinearIssue(
        id=f"linear-{ident}",
        identifier=ident,
        title=f"New {ident}",
        description="desc",
        priority=2,
        sort_order=1.0,
        state_name="Backlog",
        state_id="state-1",
        labels=["Triaged"],
        estimate=None,
        team_id="team-1",
    )


class TestIsGroupPending:
    def test_all_queued_is_pending(self):
        state = _mk_state_with_plan(
            [ExecutionGroup(parallel=["A", "B"])], {"A": "queued", "B": "queued"}
        )
        assert _is_group_pending(state, state.plan[0]) is True

    def test_any_started_is_frozen(self):
        state = _mk_state_with_plan(
            [ExecutionGroup(parallel=["A", "B"])], {"A": "queued", "B": "in_progress"}
        )
        assert _is_group_pending(state, state.plan[0]) is False

    def test_deploy_group_is_not_pending(self):
        deploy_group = ExecutionGroup(parallel=[], deploy=True)
        state = NMState.new_run({})
        state.plan = [deploy_group]
        assert _is_group_pending(state, deploy_group) is False

    def test_empty_group_is_not_pending(self):
        empty = ExecutionGroup(parallel=[])
        state = NMState.new_run({})
        state.plan = [empty]
        assert _is_group_pending(state, empty) is False


class TestApplyReplanAssignments:
    def _config(self, max_agents: int = 2) -> NMConfig:
        with patch.dict(os.environ, {"LINEAR_API_KEY": "test-key"}):
            return NMConfig(max_agents=max_agents)

    def _settings_for(self, idents: list[str]) -> dict[str, IssueSettings]:
        return {
            ident: IssueSettings(
                model="sonnet",
                effort="medium",
                touch_profile={"areas": [f"area/{ident}"], "kind": "backend", "risk_tags": []},
            )
            for ident in idents
        }

    def test_join_pending_group_when_capacity_allows(self):
        state = _mk_state_with_plan(
            [ExecutionGroup(parallel=["A"])], {"A": "queued"}
        )
        new = _mk_new_linear_issue("Z")
        added = _apply_replan_assignments(
            state,
            {"Z": new},
            {"Z": {"join": 1}},  # 1-based
            self._settings_for(["Z"]),
            self._config(max_agents=2),
            pending_indices=[0],
        )
        assert added == ["Z"]
        assert state.plan[0].parallel == ["A", "Z"]
        assert state.issues["Z"].added_via_rescan is True
        assert state.issues["Z"].ports is not None
        assert state.issues["Z"].branch.startswith("agent/Z-")

    def test_join_frozen_group_falls_back_to_new_group(self):
        state = _mk_state_with_plan(
            [ExecutionGroup(parallel=["A"])], {"A": "in_progress"}
        )
        new = _mk_new_linear_issue("Z")
        added = _apply_replan_assignments(
            state,
            {"Z": new},
            {"Z": {"join": 1}},  # group 1 is frozen
            self._settings_for(["Z"]),
            self._config(max_agents=2),
            pending_indices=[],  # nothing pending → fallback path
        )
        assert added == ["Z"]
        # The frozen group must be untouched
        assert state.plan[0].parallel == ["A"]
        # Z lands in a new appended group
        assert state.plan[-1].parallel == ["Z"]

    def test_join_target_at_capacity_falls_back(self):
        state = _mk_state_with_plan(
            [ExecutionGroup(parallel=["A", "B"])], {"A": "queued", "B": "queued"}
        )
        new = _mk_new_linear_issue("Z")
        added = _apply_replan_assignments(
            state,
            {"Z": new},
            {"Z": {"join": 1, "rationale": "no overlap"}},
            self._settings_for(["Z"]),
            self._config(max_agents=2),
            pending_indices=[0],
        )
        assert added == ["Z"]
        # Group 1 was already full; Z must be in a new appended group
        assert state.plan[0].parallel == ["A", "B"]
        assert any(g.parallel == ["Z"] for g in state.plan)

    def test_new_group_clustering_bundles_peers(self):
        state = _mk_state_with_plan(
            [ExecutionGroup(parallel=["A"])], {"A": "in_progress"}
        )
        new_x = _mk_new_linear_issue("X")
        new_y = _mk_new_linear_issue("Y")
        added = _apply_replan_assignments(
            state,
            {"X": new_x, "Y": new_y},
            {
                "X": {"new_group": "shared"},
                "Y": {"new_group": "shared"},
            },
            self._settings_for(["X", "Y"]),
            self._config(max_agents=2),
            pending_indices=[],
        )
        assert set(added) == {"X", "Y"}
        # Both land together in the same new appended group
        new_group = state.plan[-1]
        assert sorted(new_group.parallel) == ["X", "Y"]

    def test_new_group_cluster_splits_above_max_agents(self):
        state = _mk_state_with_plan([], {})
        idents = ["P", "Q", "R"]
        new_map = {i: _mk_new_linear_issue(i) for i in idents}
        added = _apply_replan_assignments(
            state,
            new_map,
            {i: {"new_group": "big"} for i in idents},
            self._settings_for(idents),
            self._config(max_agents=2),
            pending_indices=[],
        )
        assert set(added) == {"P", "Q", "R"}
        # Cluster of 3 with max_agents=2 → splits into two groups
        non_deploy = [g for g in state.plan if not g.deploy]
        sizes = sorted(len(g.parallel) for g in non_deploy)
        assert sizes == [1, 2]

    def test_new_groups_land_before_deploy_group(self):
        state = _mk_state_with_plan(
            [
                ExecutionGroup(parallel=["A"]),
                ExecutionGroup(parallel=[], deploy=True),
            ],
            {"A": "queued"},
        )
        new = _mk_new_linear_issue("Z")
        added = _apply_replan_assignments(
            state,
            {"Z": new},
            {"Z": {"new_group": "g"}},
            self._settings_for(["Z"]),
            self._config(max_agents=2),
            pending_indices=[0],
        )
        assert added == ["Z"]
        # Deploy group still last
        assert state.plan[-1].deploy is True
        # Z group sits before it
        non_deploy_idents = [g.parallel for g in state.plan if not g.deploy]
        assert ["Z"] in non_deploy_idents
        z_pos = next(i for i, g in enumerate(state.plan) if g.parallel == ["Z"])
        deploy_pos = next(i for i, g in enumerate(state.plan) if g.deploy)
        assert z_pos < deploy_pos

    def test_multiple_joins_reserve_capacity(self):
        """Two new issues both targeting the same group respect capacity."""
        state = _mk_state_with_plan(
            [ExecutionGroup(parallel=["A"])], {"A": "queued"}
        )
        new_x = _mk_new_linear_issue("X")
        new_y = _mk_new_linear_issue("Y")
        added = _apply_replan_assignments(
            state,
            {"X": new_x, "Y": new_y},
            {
                "X": {"join": 1},
                "Y": {"join": 1},
            },
            self._settings_for(["X", "Y"]),
            self._config(max_agents=2),  # group has 1 slot
            pending_indices=[0],
        )
        assert set(added) == {"X", "Y"}
        # Only one of X/Y can join group 1, the other must land in a new group
        assert len(state.plan[0].parallel) == 2  # ["A", joined-issue]
        new_groups = [g.parallel for g in state.plan[1:]]
        # The other issue is in a new appended group
        assert any(len(g) == 1 for g in new_groups)


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
