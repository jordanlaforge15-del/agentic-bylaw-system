"""Night Manager orchestrator — plan → execute → review → merge → report."""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from .config import (
    NMConfig,
    NM_DIR,
    NUDGE_FILE,
    REPO_ROOT,
    STUCK_TIMEOUT_MINUTES,
    MAX_REGRESSION_FIX_CYCLES,
)
from .linear_client import LinearClient, LinearIssue
from .state import NMState

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

        # Catch issues labeled Triaged during the planning call itself.
        if not config.issue:
            await _rescan_and_replan(state, config, linear, source="post-plan")
            state.save()

        await _execute(state, config, linear, workflow_states)

        await _generate_report(state, linear)
    finally:
        await linear.close()

    log.info("Night Manager finished. Run ID: %s", state.run_id)


async def _run_resume(config: NMConfig) -> None:
    """Resume failed/blocked issues from the last run's saved state."""
    state = NMState.load()
    if not state.run_id:
        log.error("No previous run state found. Cannot resume.")
        return

    log.info("Resuming run %s", state.run_id)

    # Determine which issues to resume
    if config.resume_issue:
        targets = [config.resume_issue]
        if config.resume_issue not in state.issues:
            log.error("Issue %s not found in last run state", config.resume_issue)
            return
        issue = state.issues[config.resume_issue]
        if not issue.is_resumable:
            log.error(
                "Issue %s is not resumable (status=%s). Only failed/blocked issues can be resumed.",
                config.resume_issue, issue.status,
            )
            return
    else:
        targets = [
            ident for ident, iss in state.issues.items() if iss.is_resumable
        ]

    if not targets:
        log.info("No resumable issues found. All issues from last run are merged or in progress.")
        return

    log.info("Resuming %d issues: %s", len(targets), targets)

    for ident in targets:
        state.issues[ident].reset_for_retry()

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


async def _rescan_and_replan(
    state: NMState,
    config: NMConfig,
    linear: LinearClient,
    source: str = "boundary",
) -> list[str]:
    """Pick up newly-Triaged issues and slot them into the in-flight plan.

    Called at each group boundary in `_execute`, and once immediately after
    the initial plan. The actual planner call only sees touch profiles for
    existing issues plus full descriptions for the new ones, keeping the
    token cost proportional to what was actually added.

    The `NUDGE_FILE` sentinel is consumed (deleted) here if present; it is
    purely a "I added work, please notice" UX hook — the rescan would have
    happened at the next boundary regardless.
    """
    from .planner import replan_with_new_issues

    nudge_present = False
    try:
        if NUDGE_FILE.exists():
            nudge_present = True
            NUDGE_FILE.unlink()
            log.info("Nudge sentinel consumed (%s)", NUDGE_FILE)
    except OSError as exc:
        log.warning("Failed to read/delete nudge sentinel: %s", exc)

    try:
        fresh = await linear.fetch_triaged_issues(config.label)
    except Exception as exc:
        log.warning("Rescan (%s) failed to fetch Linear issues: %s", source, exc)
        return []

    new_issues = [i for i in fresh if i.identifier not in state.issues]
    if not new_issues:
        if nudge_present:
            log.info("Rescan (%s): nudge received but no new Triaged issues found", source)
        return []

    log.info(
        "Rescan (%s): %d new Triaged issue(s) — %s",
        source, len(new_issues), [i.identifier for i in new_issues],
    )

    try:
        added = await replan_with_new_issues(state, new_issues, config)
    except Exception as exc:
        log.error("Replan (%s) failed: %s", source, exc)
        return []

    if not added:
        log.info("Rescan (%s): planner produced no assignments", source)
        return []

    log.info(
        "Rescan (%s): added %d issue(s) to plan — %s",
        source, len(added), added,
    )
    for ident in added:
        issue = state.issues[ident]
        try:
            await linear.post_comment(
                issue.linear_id,
                f"**Night Manager** — Added to in-flight run `{state.run_id}` "
                f"via {source} rescan.\n\n"
                f"- Branch (planned): `{issue.branch}`\n"
                f"- Ports (planned): PG={issue.ports.pg}, "
                f"API={issue.ports.api}, WEB={issue.ports.web}",
            )
        except Exception as exc:
            log.warning("Failed to post rescan-add comment for %s: %s", ident, exc)
    return added


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

    # Iterating with enumerate over state.plan is safe even when the plan
    # grows mid-loop: Python re-indexes the list each step, so groups added
    # by _rescan_and_replan after the current index get picked up in order.
    for group_idx, group in enumerate(state.plan):
        # Group-boundary rescan: pick up newly-labeled Triaged issues and
        # slot them into the remaining plan with minimal token cost.
        if not rate_limited and not config.issue:
            added = await _rescan_and_replan(
                state, config, linear, source=f"group-{group_idx + 1}",
            )
            if added:
                state.save()
                # The for-enumerate above observes the appended/inserted
                # groups naturally; refresh the local reference in case
                # the replan logic mutated this group in place.
                group = state.plan[group_idx]

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
