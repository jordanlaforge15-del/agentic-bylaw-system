"""Tests for ABS-168: pipelined review+merge in _execute.

Covers three acceptance-criteria scenarios:
(a) merge-queue ordering preserved across agent-finish ordering,
(b) failed worktree e2e in agent A does not block agent B's review,
(c) failed merge in agent A does not block agent B's merge.

These tests exercise the two new coroutines directly (_agent_through_review
and _drain_merge_queue) without spinning up real agents or git repos.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scripts.night_manager.main import _agent_through_review, _drain_merge_queue
from scripts.night_manager.state import IssuePorts, IssueState, NMState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mk_issue(ident: str, status: str = "reviewing") -> IssueState:
    issue = IssueState(
        identifier=ident,
        title=f"Test {ident}",
        branch=f"agent/{ident}-test",
        ports=IssuePorts(pg=5499, api=8099, web=3099),
        linear_id=f"linear-{ident}",
    )
    issue.status = status
    return issue


def _mk_state(*idents: str, status: str = "reviewing") -> NMState:
    state = NMState.new_run({})
    for ident in idents:
        state.issues[ident] = _mk_issue(ident, status=status)
    return state


def _mk_config():
    """Minimal NMConfig without requiring LINEAR_API_KEY in environment."""
    cfg = MagicMock()
    cfg.reviewer_model = "haiku"
    cfg.reviewer_token_limit = 0.1
    return cfg


def _mk_linear():
    linear = AsyncMock()
    linear.post_comment = AsyncMock()
    linear.update_issue_state = AsyncMock()
    return linear


def _mk_proc(exit_code: int = 0):
    """Fake asyncio.subprocess.Process that exits immediately."""
    proc = MagicMock()
    proc.returncode = exit_code
    proc.pid = 12345
    return proc


# ---------------------------------------------------------------------------
# (a) merge-queue ordering preserved across agent-finish ordering
# ---------------------------------------------------------------------------


class TestMergeQueueOrdering:
    async def test_earlier_finishing_agent_enqueued_first(self):
        """Agent that finishes review first lands first in the merge queue."""
        state = _mk_state("ABS-1", "ABS-2")
        config = _mk_config()
        linear = _mk_linear()
        workflow_states = {"In Review": "wf-review", "In Progress": "wf-progress"}
        merge_queue: asyncio.Queue = asyncio.Queue()

        finish_order: list[str] = []

        async def fake_lifecycle_slow(proc, issue, cfg, st, lin, wf):
            await asyncio.sleep(0.05)  # ABS-2 finishes second
            issue.mark_completed()
            finish_order.append(issue.identifier)

        async def fake_lifecycle_fast(proc, issue, cfg, st, lin, wf):
            await asyncio.sleep(0)    # ABS-1 finishes first
            issue.mark_completed()
            finish_order.append(issue.identifier)

        async def fake_review_gate(issue, cfg):
            return True

        with (
            patch("scripts.night_manager.main._run_agent_lifecycle") as mock_lifecycle,
            patch("scripts.night_manager.reviewer.review_and_gate", side_effect=fake_review_gate),
        ):
            # Wire each issue to its fake lifecycle
            async def dispatch_lifecycle(proc, issue, cfg, st, lin, wf):
                if issue.identifier == "ABS-1":
                    return await fake_lifecycle_fast(proc, issue, cfg, st, lin, wf)
                return await fake_lifecycle_slow(proc, issue, cfg, st, lin, wf)

            mock_lifecycle.side_effect = dispatch_lifecycle

            await asyncio.gather(
                _agent_through_review(
                    "ABS-1", _mk_proc(), config, state, linear, workflow_states, merge_queue
                ),
                _agent_through_review(
                    "ABS-2", _mk_proc(), config, state, linear, workflow_states, merge_queue
                ),
            )

        # Both issues should be in the queue; ABS-1 should be first
        assert merge_queue.qsize() == 2
        first = await merge_queue.get()
        second = await merge_queue.get()
        assert first == "ABS-1"
        assert second == "ABS-2"

    async def test_slower_agent_does_not_block_faster_from_entering_queue(self):
        """ABS-1 enters the merge queue before ABS-2's agent even finishes."""
        state = _mk_state("ABS-1", "ABS-2")
        config = _mk_config()
        linear = _mk_linear()
        workflow_states = {}
        merge_queue: asyncio.Queue = asyncio.Queue()

        enqueue_times: dict[str, float] = {}

        async def fake_lifecycle(proc, issue, cfg, st, lin, wf):
            delay = 0.01 if issue.identifier == "ABS-1" else 0.08
            await asyncio.sleep(delay)
            issue.mark_completed()

        async def fake_review_and_gate(issue, cfg):
            loop = asyncio.get_event_loop()
            enqueue_times[issue.identifier] = loop.time()
            return True

        with (
            patch("scripts.night_manager.main._run_agent_lifecycle", side_effect=fake_lifecycle),
            patch("scripts.night_manager.reviewer.review_and_gate", side_effect=fake_review_and_gate),
        ):
            await asyncio.gather(
                _agent_through_review(
                    "ABS-1", _mk_proc(), config, state, linear, workflow_states, merge_queue
                ),
                _agent_through_review(
                    "ABS-2", _mk_proc(), config, state, linear, workflow_states, merge_queue
                ),
            )

        # ABS-1 should have been enqueued (entered review) earlier than ABS-2
        assert enqueue_times["ABS-1"] < enqueue_times["ABS-2"]


# ---------------------------------------------------------------------------
# (b) failed worktree e2e in agent A does not block agent B's review
# ---------------------------------------------------------------------------


class TestFailedE2EDoesNotBlockSibling:
    async def test_failed_gate_does_not_prevent_sibling_review(self):
        """If ABS-A's review/e2e fails, ABS-B still reaches review_and_gate."""
        state = _mk_state("ABS-A", "ABS-B")
        config = _mk_config()
        linear = _mk_linear()
        workflow_states = {}
        merge_queue: asyncio.Queue = asyncio.Queue()

        reviewed = []

        async def fake_lifecycle(proc, issue, cfg, st, lin, wf):
            issue.mark_completed()

        async def fake_gate(issue, cfg):
            reviewed.append(issue.identifier)
            if issue.identifier == "ABS-A":
                issue.mark_blocked("e2e failed")
                return False
            return True

        with (
            patch("scripts.night_manager.main._run_agent_lifecycle", side_effect=fake_lifecycle),
            patch("scripts.night_manager.reviewer.review_and_gate", side_effect=fake_gate),
        ):
            await asyncio.gather(
                _agent_through_review(
                    "ABS-A", _mk_proc(), config, state, linear, workflow_states, merge_queue
                ),
                _agent_through_review(
                    "ABS-B", _mk_proc(), config, state, linear, workflow_states, merge_queue
                ),
            )

        # Both should have been reviewed despite ABS-A failing
        assert "ABS-A" in reviewed
        assert "ABS-B" in reviewed
        # ABS-B passed, so it should be in the merge queue; ABS-A should not
        assert merge_queue.qsize() == 1
        assert await merge_queue.get() == "ABS-B"

    async def test_failed_agent_does_not_enter_review(self):
        """Agent that fails (non-zero exit) does not attempt review."""
        state = _mk_state("ABS-X", "ABS-Y")
        config = _mk_config()
        linear = _mk_linear()
        workflow_states = {}
        merge_queue: asyncio.Queue = asyncio.Queue()

        reviewed = []

        async def fake_lifecycle(proc, issue, cfg, st, lin, wf):
            if issue.identifier == "ABS-X":
                issue.mark_failed("agent crashed")
            else:
                issue.mark_completed()

        async def fake_gate(issue, cfg):
            reviewed.append(issue.identifier)
            return True

        with (
            patch("scripts.night_manager.main._run_agent_lifecycle", side_effect=fake_lifecycle),
            patch("scripts.night_manager.reviewer.review_and_gate", side_effect=fake_gate),
        ):
            await asyncio.gather(
                _agent_through_review(
                    "ABS-X", _mk_proc(), config, state, linear, workflow_states, merge_queue
                ),
                _agent_through_review(
                    "ABS-Y", _mk_proc(), config, state, linear, workflow_states, merge_queue
                ),
            )

        # ABS-X failed — should not have entered review
        assert "ABS-X" not in reviewed
        # ABS-Y should have proceeded normally
        assert "ABS-Y" in reviewed
        assert merge_queue.qsize() == 1
        assert await merge_queue.get() == "ABS-Y"


# ---------------------------------------------------------------------------
# (c) failed merge in agent A does not block agent B's merge
# ---------------------------------------------------------------------------


class TestFailedMergeDoesNotBlockSibling:
    async def test_drain_continues_after_failed_merge(self):
        """_drain_merge_queue processes ABS-B even when ABS-A's merge fails."""
        state = _mk_state("ABS-A", "ABS-B")
        config = _mk_config()
        linear = _mk_linear()
        done_id = "wf-done"

        merge_order: list[str] = []

        async def fake_merge_loop(issue, cfg, st, lin):
            merge_order.append(issue.identifier)
            return issue.identifier != "ABS-A"  # ABS-A fails, ABS-B succeeds

        with patch("scripts.night_manager.main._merge_and_regression_loop", side_effect=fake_merge_loop):
            with patch("scripts.night_manager.agent.cleanup_worktree", new_callable=AsyncMock):
                queue: asyncio.Queue = asyncio.Queue()
                await queue.put("ABS-A")
                await queue.put("ABS-B")
                await queue.put(None)  # sentinel

                await _drain_merge_queue(queue, state, config, linear, done_id)

        # Both were attempted, even though ABS-A's merge failed
        assert merge_order == ["ABS-A", "ABS-B"]
        # ABS-B should be marked merged
        assert state.issues["ABS-B"].status == "merged"
        # ABS-A should remain in its failed state (not marked merged)
        assert state.issues["ABS-A"].status != "merged"

    async def test_drain_marks_done_in_linear_for_successful_merge(self):
        """_drain_merge_queue calls update_issue_state with done_id on success."""
        state = _mk_state("ABS-Z")
        config = _mk_config()
        linear = _mk_linear()
        done_id = "wf-done"

        async def fake_merge_loop(issue, cfg, st, lin):
            return True

        with (
            patch("scripts.night_manager.main._merge_and_regression_loop", side_effect=fake_merge_loop),
            patch("scripts.night_manager.agent.cleanup_worktree", new_callable=AsyncMock),
        ):
            queue: asyncio.Queue = asyncio.Queue()
            await queue.put("ABS-Z")
            await queue.put(None)

            await _drain_merge_queue(queue, state, config, linear, done_id)

        linear.update_issue_state.assert_awaited_once_with("linear-ABS-Z", done_id)
        assert state.issues["ABS-Z"].status == "merged"

    async def test_drain_sentinel_stops_loop(self):
        """_drain_merge_queue returns cleanly on None sentinel with no items."""
        state = _mk_state()
        config = _mk_config()
        linear = _mk_linear()

        queue: asyncio.Queue = asyncio.Queue()
        await queue.put(None)  # immediate sentinel

        # Should return without error
        await _drain_merge_queue(queue, state, config, linear, None)

        linear.post_comment.assert_not_called()
