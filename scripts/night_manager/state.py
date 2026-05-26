"""Persistent state management for Night Manager runs."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import NM_DIR, LOGS_DIR, STATE_FILE

log = logging.getLogger("night_manager.state")


@dataclass
class IssuePorts:
    pg: int
    api: int
    web: int


@dataclass
class IssueState:
    identifier: str
    title: str
    status: str = "queued"
    branch: str = ""
    worktree: str = ""
    ports: IssuePorts | None = None
    session_id: str = ""
    pid: int = 0
    log_file: str = ""
    attempts: int = 0
    review_attempts: int = 0
    started_at: str | None = None
    completed_at: str | None = None
    merged_at: str | None = None
    error: str | None = None
    rate_limited: bool = False
    agent_model: str = ""
    agent_effort: str = ""
    linear_id: str = ""
    # Compact summary of the issue produced by the planner so it can be
    # reused for in-flight replans without re-sending the full description.
    # Shape: {"areas": [str], "kind": str, "risk_tags": [str]}
    touch_profile: dict | None = None
    # True once this issue was added to a run after the initial plan via
    # a rescan at a group boundary. Purely informational.
    added_via_rescan: bool = False

    def mark_started(self) -> None:
        self.status = "in_progress"
        self.started_at = _now_iso()
        self.attempts += 1

    def mark_completed(self) -> None:
        self.status = "reviewing"
        self.completed_at = _now_iso()

    def mark_merged(self) -> None:
        self.status = "merged"
        self.merged_at = _now_iso()

    def mark_failed(self, error: str) -> None:
        self.status = "failed"
        self.error = error

    def mark_blocked(self, reason: str) -> None:
        self.status = "blocked"
        self.error = reason

    @property
    def is_resumable(self) -> bool:
        return self.status in ("failed", "blocked")

    def reset_for_retry(self) -> None:
        self.status = "queued"
        self.error = None
        self.rate_limited = False
        self.completed_at = None
        self.merged_at = None


@dataclass
class ExecutionGroup:
    parallel: list[str] = field(default_factory=list)
    deploy: bool = False


@dataclass
class NMState:
    run_id: str = ""
    started_at: str = ""
    config: dict[str, Any] = field(default_factory=dict)
    plan: list[ExecutionGroup] = field(default_factory=list)
    issues: dict[str, IssueState] = field(default_factory=dict)

    def save(self, path: Path | None = None) -> None:
        target = path or STATE_FILE
        target.parent.mkdir(parents=True, exist_ok=True)
        data = _serialize(self)
        fd, tmp = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
        try:
            os.write(fd, json.dumps(data, indent=2).encode())
            os.close(fd)
            os.replace(tmp, target)
        except BaseException:
            os.close(fd) if not os.get_inheritable(fd) else None
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
        log.debug("State saved to %s", target)

    @classmethod
    def load(cls, path: Path | None = None) -> NMState:
        target = path or STATE_FILE
        if not target.exists():
            return cls()
        data = json.loads(target.read_text())
        return _deserialize(data)

    @classmethod
    def new_run(cls, config: dict[str, Any]) -> NMState:
        now = _now_iso()
        run_id = f"nm-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
        NM_DIR.mkdir(parents=True, exist_ok=True)
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        return cls(run_id=run_id, started_at=now, config=config)

    def active_agent_count(self) -> int:
        return sum(1 for s in self.issues.values() if s.status == "in_progress")

    def pending_issues(self) -> list[str]:
        return [ident for ident, s in self.issues.items() if s.status == "queued"]

    def summary(self) -> dict[str, list[str]]:
        buckets: dict[str, list[str]] = {}
        for ident, s in self.issues.items():
            buckets.setdefault(s.status, []).append(ident)
        return buckets


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _serialize(state: NMState) -> dict[str, Any]:
    data = {
        "run_id": state.run_id,
        "started_at": state.started_at,
        "config": state.config,
        "plan": [asdict(g) for g in state.plan],
        "issues": {k: asdict(v) for k, v in state.issues.items()},
    }
    return data


def _deserialize(data: dict[str, Any]) -> NMState:
    plan = [
        ExecutionGroup(
            parallel=g.get("parallel", []),
            deploy=g.get("deploy", False),
        )
        for g in data.get("plan", [])
    ]
    issues = {}
    for k, v in data.get("issues", {}).items():
        ports_raw = v.pop("ports", None)
        ports = IssuePorts(**ports_raw) if ports_raw else None
        issues[k] = IssueState(ports=ports, **v)
    return NMState(
        run_id=data.get("run_id", ""),
        started_at=data.get("started_at", ""),
        config=data.get("config", {}),
        plan=plan,
        issues=issues,
    )
