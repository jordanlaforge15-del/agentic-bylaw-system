"""Night Manager orchestrator — plan → execute → review → merge → report."""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from .config import NMConfig, NM_DIR, REPO_ROOT, STUCK_TIMEOUT_MINUTES
from .linear_client import LinearClient, LinearIssue
from .state import NMState

log = logging.getLogger("night_manager")


async def run(config: NMConfig) -> None:
    _setup_logging()
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


async def _execute(
    state: NMState,
    config: NMConfig,
    linear: LinearClient,
    workflow_states: dict[str, str],
) -> None:
    from .agent import spawn_agent, monitor_agent, cleanup_worktree, setup_worktree
    from .reviewer import review_and_gate, merge_to_dev, run_dev_e2e, revert_merge

    in_progress_id = workflow_states.get("In Progress")
    in_review_id = workflow_states.get("In Review")
    done_id = workflow_states.get("Done")

    for group_idx, group in enumerate(state.plan):
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

            success, msg = await merge_to_dev(issue)
            if not success:
                issue.mark_failed(msg)
                state.save()
                await linear.post_comment(issue.linear_id, f"**Night Manager** — Merge FAILED\n\n{msg}")
                continue

            log.info("Running regression e2e on dev after merging %s", ident)
            reg_passed, reg_output = await run_dev_e2e()
            if not reg_passed:
                log.warning("Regression detected after merging %s, reverting", ident)
                rev_ok, rev_msg = await revert_merge(issue)
                issue.mark_failed(f"Post-merge regression: reverted. {rev_msg}")
                state.save()
                await linear.post_comment(
                    issue.linear_id,
                    f"**Night Manager** — Post-merge regression detected, merge reverted.\n\n"
                    f"```\n{reg_output[-2000:]}\n```",
                )
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
        issue.mark_failed(f"Agent exited with code {exit_code}")
        state.save()
        await linear.post_comment(
            issue.linear_id,
            f"**Night Manager** — Agent FAILED (exit={exit_code})\n\n"
            f"Last output:\n```\n{final_output[-2000:]}\n```",
        )
        return

    issue.mark_completed()
    state.save()

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
