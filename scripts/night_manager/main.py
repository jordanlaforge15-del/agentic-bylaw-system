"""Night Manager orchestrator — plan → execute → review → merge → report."""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from .config import NMConfig, NM_DIR, REPO_ROOT, STUCK_TIMEOUT_MINUTES, MAX_REGRESSION_FIX_CYCLES
from .linear_client import LinearClient, LinearIssue
from .state import IssueState, NMState, _now_iso

log = logging.getLogger("night_manager")


async def run(config: NMConfig) -> None:
    _setup_logging()

    if config.resume or config.resume_issue:
        await _run_resume(config)
        return

    log.info("Night Manager starting (dry_run=%s, max_agents=%d)", config.dry_run, config.max_agents)

    linear = LinearClient(config.linear_api_key)
    try:
        issues = await _fetch_issues(linear, config)
        if not issues:
            log.info("No issues found. Exiting.")
            return

        state = await _plan(issues, config)
        state.save()

        workflow_states = await linear.get_workflow_states()

        if config.dry_run:
            await _dry_run_report(state, linear)
            return

        await _execute(state, config, linear, workflow_states)

        await _generate_report(state, linear)
    finally:
        await linear.close()

    log.info("Night Manager finished. Run ID: %s", state.run_id)


async def _run_resume(config: NMConfig) -> None:
    """Resume failed/blocked/reviewing issues from the last run's saved state.

    Before selecting targets, this performs three reconciliation passes
    against ground truth — fixing the drift that bit us in run
    nm-20260526-004610:

    1. **State drift vs git** (Gap 3): if a non-merged issue's branch is
       already on dev (out-of-band merge) or its tip == merge-base
       (zombie — merged then reverted), reclassify it. Otherwise the
       resume would re-do already-merged work.
    2. **Stale-PID detection** (Gap 2): an `in_progress` or `reviewing`
       issue whose recorded PID is dead (kernel panic, SIGKILL, etc.)
       is reclassified to `failed` so the resume loop re-spawns rather
       than waiting on a corpse.
    3. **Status-based filtering** (Gap 1): the resumable set is
       `failed | blocked | reviewing`, plus `queued` only when the
       operator passes `--resume-queued`.
    """
    state = NMState.load()
    if not state.run_id:
        log.error("No previous run state found. Cannot resume.")
        return

    log.info("Resuming run %s", state.run_id)

    # --- Pass 1: reconcile state.json against git reality on dev. -----
    reconciled = _reconcile_state_with_git(state)
    if reconciled:
        log.info("Reconciled %d issues against dev: %s", len(reconciled), reconciled)
        state.save()

    # --- Pass 2: detect stale PIDs from in_progress / reviewing. -------
    from .agent import is_pid_alive
    stale = _detect_stale_pids(state, is_pid_alive)
    if stale:
        log.info("Detected %d orphaned issues (dead PID): %s", len(stale), stale)
        state.save()

    # --- Pass 3: pick targets based on flags. --------------------------
    if config.resume_issue:
        if config.resume_issue not in state.issues:
            log.error("Issue %s not found in last run state", config.resume_issue)
            return
        issue = state.issues[config.resume_issue]
        # --resume-issue is an explicit operator request — accept any
        # non-merged status, including queued. The sanity-check pass
        # has already reclassified out-of-band merges.
        if issue.status == "merged":
            log.error(
                "Issue %s is already merged — nothing to resume.",
                config.resume_issue,
            )
            return
        targets = [config.resume_issue]
    else:
        targets = [
            ident for ident, iss in state.issues.items() if iss.is_resumable
        ]
        if config.resume_queued:
            queued = [
                ident for ident, iss in state.issues.items()
                if iss.status == "queued"
            ]
            if queued:
                log.info("--resume-queued: also picking up %d queued issues: %s",
                         len(queued), queued)
                targets.extend(queued)

    if not targets:
        log.info("No resumable issues found. All issues from last run are merged or in progress.")
        return

    log.info("Resuming %d issues: %s", len(targets), targets)

    for ident in targets:
        issue = state.issues[ident]
        # If the issue has a recorded session_id and a live worktree, the
        # resume path will use `claude --resume <session_id>` which is
        # durable across process restarts. The PID we record is the new
        # subprocess's, not the dead one. reset_for_retry zeroes the
        # status; we deliberately keep session_id so resume can re-attach.
        issue.reset_for_retry()
        # Clear the stale PID so nothing downstream treats it as alive.
        issue.pid = 0

    # Rebuild the execution plan keeping only groups that contain resumable issues
    resumed_plan = []
    for group in state.plan:
        if group.deploy:
            continue
        active = [i for i in group.parallel if i in targets]
        if active:
            from .state import ExecutionGroup
            resumed_plan.append(ExecutionGroup(parallel=active))

    state.plan = resumed_plan
    state.save()

    log.info("Resumed execution plan:")
    for i, group in enumerate(resumed_plan):
        log.info("  Group %d: %s (parallel)", i + 1, group.parallel)

    linear = LinearClient(config.linear_api_key)
    try:
        workflow_states = await linear.get_workflow_states()
        await _execute(state, config, linear, workflow_states)
        await _generate_report(state, linear)
    finally:
        await linear.close()

    log.info("Resume run finished. Original run ID: %s", state.run_id)


def _git_oneline(*args: str, cwd: Path | None = None) -> str:
    """Run a git command synchronously and return stdout (empty on error).

    Resume happens before the event loop is doing anything else, so a
    synchronous subprocess call here is fine and far simpler than the
    asyncio variant. Never raises — git failures (missing branch,
    unknown ref) just yield "".
    """
    import subprocess
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd or REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _reconcile_state_with_git(state: NMState) -> list[str]:
    """Self-heal state.json against the current dev branch.

    For each issue in a non-terminal status, check three things in order:

    1. Branch already merged into dev (the ABS-125 case — merged by hand
       outside NM): mark as `merged`.
    2. Commit referencing the issue identifier (e.g. "[ABS-125]" in the
       subject) is on dev: same — mark as `merged`. This catches squash
       merges that drop the branch ref.
    3. Branch tip == merge-base with dev (the ABS-129 / ABS-135 zombie
       case — merged then reverted): mark as `blocked` with a clear
       reason. The branch has nothing new to merge.

    Returns the list of identifiers that were reclassified so the caller
    can log them and persist the state.
    """
    reconciled: list[str] = []

    # Branches actually merged into dev (one-shot list — avoids forking
    # `git branch --merged dev | grep` per issue).
    merged_raw = _git_oneline("branch", "--merged", "dev")
    merged_branches = {
        b.strip().lstrip("* ").strip()
        for b in merged_raw.splitlines()
        if b.strip()
    }

    for ident, issue in state.issues.items():
        if issue.status not in IssueState.RECONCILABLE_STATUSES:
            continue
        if not issue.branch:
            # Queued issue may not have a branch yet — skip.
            continue

        tip = _git_oneline("rev-parse", "--verify", "--quiet", issue.branch)
        base = _git_oneline("merge-base", "dev", issue.branch) if tip else ""
        merged_into_dev = issue.branch in merged_branches

        # Look for a "Revert ... <identifier> ..." commit on dev. After
        # `git revert -m 1 <merge-of-ABS-X>` the revert commit's subject
        # is `Revert "Merge ABS-X: ..."`. Matching on both literal
        # `Revert` and the identifier scopes this tightly enough.
        revert_marker = _git_oneline(
            "log", "dev", "--oneline", "-n", "50",
            f"--grep=Revert.*{ident}",
        )
        is_zombie = (
            tip and base and tip == base
            and (bool(revert_marker) or not merged_into_dev)
        )

        # 1. Zombie detection comes FIRST: branch tip == merge-base AND
        #    either the merge was reverted on dev (ABS-129 / ABS-135
        #    case from nm-20260526-004610) OR git doesn't list the
        #    branch as merged at all (cherry-pick / rebase orphan).
        #    A clean merged branch ALSO has tip == merge-base when no
        #    new commits happened after the merge, so the revert marker
        #    is the distinguishing signal.
        if is_zombie:
            log.info(
                "reconciled %s as zombie branch (tip == merge-base with dev; "
                "revert_marker=%r)", ident, bool(revert_marker),
            )
            issue.mark_blocked(
                "zombie branch: tip == merge-base; needs re-plan"
            )
            reconciled.append(ident)
            continue

        # 2. Branch merged into dev (no revert in play).
        if merged_into_dev:
            log.info(
                "reconciled %s as already-on-dev (branch %s merged into dev)",
                ident, issue.branch,
            )
            issue.status = "merged"
            issue.merged_at = _now_iso()
            issue.error = None
            reconciled.append(ident)
            continue

        # 3. Commit subject referencing the issue identifier on dev.
        # Use --grep with a fixed string and a generous lookback so we
        # don't miss squash-merged commits whose branch was deleted.
        subject_match = _git_oneline(
            "log", "dev", "--oneline", "-n", "50",
            f"--grep={ident}",
        )
        if subject_match:
            # Only treat as merged if the issue is currently `queued`,
            # `failed`, or `blocked` — i.e. has no recorded merged_at.
            # An `in_progress` issue with a commit on dev is more likely
            # a coincidence (an unrelated commit mentioning the ID), so
            # be conservative and don't auto-merge it.
            if issue.status in ("queued", "failed", "blocked"):
                log.info(
                    "reconciled %s as already-on-dev "
                    "(commit on dev mentions %s: %s)",
                    ident, ident, subject_match.splitlines()[0][:80],
                )
                issue.status = "merged"
                issue.merged_at = _now_iso()
                issue.error = None
                reconciled.append(ident)
                continue

    return reconciled


def _detect_stale_pids(
    state: NMState,
    is_pid_alive_fn,
) -> list[str]:
    """Reclassify `in_progress` / `reviewing` issues with dead PIDs.

    `is_pid_alive_fn` is injected so tests can pass a fake without
    importing the agent module. Returns the list of identifiers that
    were reclassified.
    """
    stale: list[str] = []
    for ident, issue in state.issues.items():
        if issue.status not in ("in_progress", "reviewing"):
            continue
        if issue.pid <= 0:
            # No PID recorded — can't tell if it's stale. Don't touch.
            continue
        if is_pid_alive_fn(issue.pid):
            continue
        log.info(
            "orphan: %s has status=%s but pid=%d is dead — reclassifying as failed",
            ident, issue.status, issue.pid,
        )
        issue.mark_failed(
            f"Orphaned: PID {issue.pid} was dead at resume "
            f"(prior status={issue.status})"
        )
        # Don't zero pid here — _run_resume does it after reset_for_retry.
        stale.append(ident)
    return stale


async def _fetch_issues(linear: LinearClient, config: NMConfig) -> list[LinearIssue]:
    if config.issue:
        issue = await linear.search_issue_by_identifier(config.issue)
        if not issue:
            log.error("Issue %s not found", config.issue)
            return []
        log.info("Single-issue mode: %s — %s", issue.identifier, issue.title)
        return [issue]

    issues = await linear.fetch_triaged_issues(config.label)
    log.info("Found %d triaged issues", len(issues))
    for i in issues:
        log.info("  %s: %s (priority=%d)", i.identifier, i.title, i.priority)
    return issues


async def _plan(issues: list[LinearIssue], config: NMConfig) -> NMState:
    from .planner import plan_execution, build_state_from_plan

    log.info("Planning execution for %d issues...", len(issues))
    plan = await plan_execution(issues, config)
    state = build_state_from_plan(plan, issues, config)

    log.info("Execution plan:")
    for i, group in enumerate(state.plan):
        if group.deploy:
            log.info("  Group %d: [DEPLOY]", i + 1)
        else:
            log.info("  Group %d: %s (parallel)", i + 1, group.parallel)

    return state


async def _dry_run_report(state: NMState, linear: LinearClient) -> None:
    report_lines = [
        f"# Night Manager Dry Run — {state.run_id}",
        f"Started: {state.started_at}",
        "",
        "## Execution Plan",
    ]
    for i, group in enumerate(state.plan):
        if group.deploy:
            report_lines.append(f"### Group {i + 1}: Deploy")
        else:
            report_lines.append(f"### Group {i + 1}: Parallel")
            for ident in group.parallel:
                issue = state.issues[ident]
                report_lines.append(
                    f"- **{ident}**: {issue.title}\n"
                    f"  - Branch: `{issue.branch}`\n"
                    f"  - Ports: PG={issue.ports.pg}, API={issue.ports.api}, WEB={issue.ports.web}"
                )
    report_lines.append("")
    report_lines.append("## Issues")
    for ident, issue in state.issues.items():
        report_lines.append(f"- {ident}: {issue.title} [{issue.status}]")

    report = "\n".join(report_lines)
    report_path = NM_DIR / f"report-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.md"
    report_path.write_text(report)
    log.info("Dry run report written to %s", report_path)
    print(report)


RATE_LIMIT_PROBE_WAIT = 300  # seconds to wait before retrying after rate limit
RATE_LIMIT_MAX_RETRIES = 6  # ~30 min total wait


async def _check_rate_limit() -> tuple[bool, str]:
    """Run a minimal Claude probe to check if we're rate-limited.

    Returns (available, message). available=True means quota is usable.
    """
    proc = await asyncio.create_subprocess_exec(
        "claude", "-p",
        "--model", "haiku",
        "--max-budget-usd", "0.05",
        "respond with only the word OK",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    output = stdout.decode("utf-8", errors="replace").strip()
    error = stderr.decode("utf-8", errors="replace").strip()

    if proc.returncode == 0:
        return True, "OK"

    combined = f"{output} {error}"
    if _is_rate_limited(combined):
        return False, combined
    return True, "Probe failed but not rate-limited, proceeding"


async def _wait_for_rate_limit(group_idx: int, total_groups: int) -> bool:
    """Wait for rate limit to clear, polling periodically.

    Returns True if quota became available, False if retries exhausted.
    """
    for attempt in range(1, RATE_LIMIT_MAX_RETRIES + 1):
        log.info(
            "Rate limited before group %d/%d — waiting %ds (attempt %d/%d)",
            group_idx + 1, total_groups, RATE_LIMIT_PROBE_WAIT,
            attempt, RATE_LIMIT_MAX_RETRIES,
        )
        await asyncio.sleep(RATE_LIMIT_PROBE_WAIT)
        available, msg = await _check_rate_limit()
        if available:
            log.info("Rate limit cleared, resuming")
            return True
        log.info("Still rate limited: %s", msg[:120])
    return False


async def _execute(
    state: NMState,
    config: NMConfig,
    linear: LinearClient,
    workflow_states: dict[str, str],
) -> None:
    from .agent import spawn_agent, monitor_agent, cleanup_worktree, setup_worktree, resume_agent
    from .reviewer import review_and_gate, merge_to_dev, run_dev_e2e, run_e2e, revert_merge, restore_worktree_branch

    in_progress_id = workflow_states.get("In Progress")
    in_review_id = workflow_states.get("In Review")
    done_id = workflow_states.get("Done")

    rate_limited = False

    for group_idx, group in enumerate(state.plan):
        if rate_limited:
            log.warning(
                "Skipping group %d/%d — rate limited in prior group",
                group_idx + 1, len(state.plan),
            )
            for ident in group.parallel:
                issue = state.issues[ident]
                issue.mark_failed("Skipped: rate limited in prior group")
                issue.rate_limited = True
            state.save()
            continue

        if group.deploy:
            if config.deploy:
                log.info("Group %d: Deployment phase", group_idx + 1)
                await _deploy(state, config, linear)
            else:
                log.info("Group %d: Deploy skipped (--deploy not set)", group_idx + 1)
            continue

        log.info(
            "=== Group %d/%d: %s ===",
            group_idx + 1, len(state.plan), group.parallel,
        )

        available, probe_msg = await _check_rate_limit()
        if not available:
            log.warning("Rate limit detected before group %d: %s", group_idx + 1, probe_msg[:120])
            cleared = await _wait_for_rate_limit(group_idx, len(state.plan))
            if not cleared:
                log.error("Rate limit did not clear after %d retries, marking remaining groups as rate-limited",
                          RATE_LIMIT_MAX_RETRIES)
                rate_limited = True
                for ident in group.parallel:
                    state.issues[ident].mark_failed("Rate limited — quota did not clear")
                    state.issues[ident].rate_limited = True
                state.save()
                continue

        agent_tasks: dict[str, asyncio.Task] = {}

        for ident in group.parallel:
            issue = state.issues[ident]
            issue_data = None
            try:
                li = await linear.search_issue_by_identifier(ident)
                if li:
                    issue_data = li.description
            except Exception:
                pass

            worktree_path = await setup_worktree(issue, state)
            state.save()

            if in_progress_id:
                try:
                    await linear.update_issue_state(issue.linear_id, in_progress_id)
                except Exception as e:
                    log.warning("Failed to update Linear status for %s: %s", ident, e)

            await linear.post_comment(
                issue.linear_id,
                f"**Night Manager** — Agent started\n\n"
                f"- Branch: `{issue.branch}`\n"
                f"- Worktree: `{issue.worktree}`\n"
                f"- Ports: PG={issue.ports.pg}, API={issue.ports.api}, WEB={issue.ports.web}\n"
                f"- Run: `{state.run_id}`",
            )

            proc = await spawn_agent(issue, config, issue_data)
            state.save()

            task = asyncio.create_task(
                _run_agent_lifecycle(proc, issue, config, state, linear, workflow_states),
                name=f"agent-{ident}",
            )
            agent_tasks[ident] = task

        if agent_tasks:
            results = await asyncio.gather(*agent_tasks.values(), return_exceptions=True)
            for ident, result in zip(agent_tasks.keys(), results):
                if isinstance(result, Exception):
                    log.error("Agent %s raised exception: %s", ident, result)
                    state.issues[ident].mark_failed(str(result))
                    state.save()

        if any(state.issues[i].rate_limited for i in group.parallel):
            rate_limited = True

        # Sequential merge phase for this group
        for ident in group.parallel:
            issue = state.issues[ident]
            if issue.status != "reviewing":
                log.info("Skipping merge for %s (status=%s)", ident, issue.status)
                continue

            log.info("Review + merge gate for %s", ident)
            gate_passed = await review_and_gate(issue, config)
            state.save()

            if not gate_passed:
                log.warning("Gate failed for %s: %s", ident, issue.error)
                await linear.post_comment(
                    issue.linear_id,
                    f"**Night Manager** — Gate FAILED\n\n{issue.error}",
                )
                continue

            merged = await _merge_and_regression_loop(
                issue, config, state, linear,
            )
            if not merged:
                continue

            issue.mark_merged()
            state.save()

            if done_id:
                try:
                    await linear.update_issue_state(issue.linear_id, done_id)
                except Exception as e:
                    log.warning("Failed to update Linear status for %s: %s", ident, e)

            await linear.post_comment(
                issue.linear_id,
                f"**Night Manager** — Merged to dev and regression tests passed.",
            )

            await cleanup_worktree(issue)
            state.save()

    log.info("All groups processed.")


RATE_LIMIT_MARKERS = ["session limit", "rate limit", "usage limit"]


def _is_rate_limited(error_text: str) -> bool:
    lower = error_text.lower()
    return any(m in lower for m in RATE_LIMIT_MARKERS)


async def _merge_and_regression_loop(
    issue,
    config: NMConfig,
    state: NMState,
    linear,
) -> bool:
    """Merge the issue into dev and run regression e2e.

    On regression failure, reverts the merge, resumes the agent to fix,
    and retries up to MAX_REGRESSION_FIX_CYCLES times.
    Returns True if merged and regression passed.
    """
    from .agent import resume_agent, monitor_agent
    from .reviewer import (
        merge_to_dev, run_dev_e2e, run_e2e,
        revert_merge, restore_worktree_branch,
    )

    success, msg = await merge_to_dev(issue)
    if not success:
        issue.mark_failed(msg)
        state.save()
        await linear.post_comment(
            issue.linear_id, f"**Night Manager** — Merge FAILED\n\n{msg}",
        )
        return False

    for reg_cycle in range(MAX_REGRESSION_FIX_CYCLES + 1):
        log.info(
            "Regression e2e for %s (attempt %d/%d)",
            issue.identifier, reg_cycle + 1, MAX_REGRESSION_FIX_CYCLES + 1,
        )
        reg_passed, reg_output = await run_dev_e2e(issue)
        if reg_passed:
            return True

        log.warning(
            "Regression detected after merging %s (attempt %d/%d), reverting",
            issue.identifier, reg_cycle + 1, MAX_REGRESSION_FIX_CYCLES + 1,
        )
        await revert_merge(issue)

        if reg_cycle >= MAX_REGRESSION_FIX_CYCLES:
            issue.mark_failed(
                f"Post-merge regression after {MAX_REGRESSION_FIX_CYCLES + 1} attempts",
            )
            state.save()
            await linear.post_comment(
                issue.linear_id,
                f"**Night Manager** — Post-merge regression, exhausted "
                f"{MAX_REGRESSION_FIX_CYCLES + 1} fix attempts.\n\n"
                f"```\n{reg_output[-2000:]}\n```",
            )
            return False

        await restore_worktree_branch(issue)

        feedback = (
            f"Post-merge regression detected on dev after merging {issue.identifier}. "
            f"The merge was reverted. Fix the failing tests and commit.\n\n"
            f"Regression test output:\n{reg_output[-3000:]}"
        )
        log.info(
            "Sending regression feedback to agent for %s (fix cycle %d/%d)",
            issue.identifier, reg_cycle + 1, MAX_REGRESSION_FIX_CYCLES,
        )
        proc = await resume_agent(issue, config, feedback)
        exit_code, _ = await monitor_agent(proc, issue, STUCK_TIMEOUT_MINUTES)

        if exit_code != 0:
            issue.mark_failed(f"Agent failed during regression fix (exit={exit_code})")
            state.save()
            await linear.post_comment(
                issue.linear_id,
                f"**Night Manager** — Agent failed during regression fix cycle "
                f"{reg_cycle + 1} (exit={exit_code})",
            )
            return False

        wt_passed, wt_output = await run_e2e(issue)
        if not wt_passed:
            issue.mark_failed(
                f"Worktree e2e still failing after regression fix cycle {reg_cycle + 1}",
            )
            state.save()
            await linear.post_comment(
                issue.linear_id,
                f"**Night Manager** — Worktree e2e failed after regression fix "
                f"cycle {reg_cycle + 1}.\n\n```\n{wt_output[-2000:]}\n```",
            )
            return False

        success, msg = await merge_to_dev(issue)
        if not success:
            issue.mark_failed(msg)
            state.save()
            await linear.post_comment(
                issue.linear_id,
                f"**Night Manager** — Re-merge FAILED after fix cycle "
                f"{reg_cycle + 1}\n\n{msg}",
            )
            return False

    return False  # unreachable


async def _run_agent_lifecycle(
    proc: asyncio.subprocess.Process,
    issue,
    config: NMConfig,
    state: NMState,
    linear: LinearClient,
    workflow_states: dict[str, str],
) -> None:
    from .agent import monitor_agent

    exit_code, final_output = await monitor_agent(proc, issue, STUCK_TIMEOUT_MINUTES)

    if exit_code != 0:
        error_detail = final_output
        if not error_detail and issue.log_file:
            try:
                log_content = Path(issue.log_file).read_text()
                error_detail = log_content.strip()
            except OSError:
                error_detail = "(no log output captured)"

        if _is_rate_limited(error_detail):
            issue.mark_failed(f"Rate limited: {error_detail[:200]}")
            issue.rate_limited = True
            log.warning("Agent %s hit rate limit — remaining group issues will also fail", issue.identifier)
        else:
            issue.mark_failed(f"Agent exited with code {exit_code}: {error_detail[:500]}")

        state.save()
        await linear.post_comment(
            issue.linear_id,
            f"**Night Manager** — Agent FAILED (exit={exit_code})\n\n"
            f"```\n{error_detail[-2000:]}\n```",
        )
        return

    issue.mark_completed()
    state.save()

    in_review_id = workflow_states.get("In Review")
    if in_review_id:
        try:
            await linear.update_issue_state(issue.linear_id, in_review_id)
        except Exception as e:
            log.warning("Failed to update Linear status for %s: %s", issue.identifier, e)

    await linear.post_comment(
        issue.linear_id,
        f"**Night Manager** — Agent completed, entering review.\n\n"
        f"Summary:\n{final_output[-1000:]}",
    )


async def _deploy(state: NMState, config: NMConfig, linear: LinearClient) -> None:
    log.info("Deployment phase — promoting dev → main via test-and-deploy-bylaw pattern")
    # Deployment uses a claude -p subprocess invoking the test-and-deploy-bylaw skill
    proc = await asyncio.create_subprocess_exec(
        "claude", "-p",
        "--output-format", "stream-json",
        "--permission-mode", "acceptEdits",
        "--model", config.model,
        "/test-and-deploy-bylaw",
        cwd=str(REPO_ROOT),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        log.error("Deployment failed (exit=%d)", proc.returncode)
        log.error("stderr: %s", stderr.decode()[:2000])
    else:
        log.info("Deployment completed successfully")


async def _generate_report(state: NMState, linear: LinearClient) -> None:
    summary = state.summary()
    now = datetime.now(timezone.utc)

    report_lines = [
        f"# Night Manager Report — {state.run_id}",
        f"Generated: {now.isoformat()}",
        f"Started: {state.started_at}",
        "",
        "## Results",
        "",
    ]

    for status, idents in sorted(summary.items()):
        report_lines.append(f"### {status.upper()} ({len(idents)})")
        for ident in idents:
            issue = state.issues[ident]
            extra = f" — {issue.error}" if issue.error else ""
            report_lines.append(f"- **{ident}**: {issue.title}{extra}")
        report_lines.append("")

    merged = summary.get("merged", [])
    failed = summary.get("failed", [])
    blocked = summary.get("blocked", [])

    report_lines.extend([
        "## Summary",
        f"- Merged: {len(merged)}",
        f"- Failed: {len(failed)}",
        f"- Blocked: {len(blocked)}",
        f"- Total issues: {len(state.issues)}",
    ])

    if blocked or failed:
        report_lines.extend([
            "",
            "## Action Required",
            "The following issues need human attention:",
        ])
        for ident in blocked + failed:
            issue = state.issues[ident]
            report_lines.append(f"- **{ident}**: {issue.title} [{issue.status}] — {issue.error}")

    report = "\n".join(report_lines)
    report_path = NM_DIR / f"report-{now.strftime('%Y%m%d-%H%M%S')}.md"
    report_path.write_text(report)
    log.info("Report written to %s", report_path)

    state.save()


def _setup_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(
        "[%(asctime)s] %(name)s %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    ))
    root = logging.getLogger("night_manager")
    root.setLevel(logging.INFO)
    root.addHandler(handler)

    NM_DIR.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(NM_DIR / "night_manager.log")
    file_handler.setFormatter(logging.Formatter(
        "[%(asctime)s] %(name)s %(levelname)s: %(message)s",
    ))
    root.addHandler(file_handler)
