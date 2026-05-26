"""Dev agent lifecycle: spawn, stream, detect completion, resume."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import uuid
from pathlib import Path
from typing import Any

from .config import (
    ALLOWED_TOOLS,
    BASH_TOOL_BUDGET_MINUTES,
    DEV_AGENT_SYSTEM_PROMPT,
    NMConfig,
    PORT_BASE_API,
    PORT_BASE_PG,
    PORT_BASE_WEB,
    POST_SUCCESS_IDLE_MINUTES,
    REPO_ROOT,
    STUCK_TIMEOUT_MINUTES,
    WORKTREE_ROOT,
)
from .reviewer import _combine_streams
from .state import IssueState, NMState

log = logging.getLogger("night_manager.agent")

# Bash-aware grace strategy: per-tool budget (not PID/CPU liveness).
# Rationale: claude-cli spawns Bash subprocesses in its own process tree
# that we don't own, so /proc-style liveness would require psutil and a
# cross-platform tree walk. Per-tool budget is event-only — we already
# parse every stream-json line, so tracking the last unterminated tool_use
# and giving Bash an extended ceiling stays in the same code path with no
# new dependencies and is trivial to unit-test with a fake clock.


DEV_AGENT_PROMPT_TEMPLATE = """\
Work on Linear issue {identifier}: {title}

{description}

You are working in worktree: {worktree_path}
Your branch: {branch}
Port configuration: PG_PORT={pg_port} E2E_FASTAPI_PORT={api_port} E2E_WEB_PORT={web_port}

Follow the SDLC in CLAUDE.md. When complete:
1. Ensure all e2e tests pass (export the port env vars above before running tests)
2. Commit all changes with descriptive messages prefixed [{identifier}]
3. Exit with a summary of what you implemented and test results.
   Do NOT call any Linear/MCP tools — the Night Manager handles all Linear updates.\
"""


CONTINUATION_PROMPT_TEMPLATE = """\
Continue work on Linear issue {identifier}: {title}

You already did one pass on this issue in this worktree — your prior commits
are visible via `git log dev..HEAD`. The Night Manager is invoking you again
because a downstream gate (code review, worktree e2e, or post-merge regression)
flagged something that needs to be fixed.

Worktree: {worktree_path}
Branch:   {branch}
Port configuration: PG_PORT={pg_port} E2E_FASTAPI_PORT={api_port} E2E_WEB_PORT={web_port}

=== Feedback from the failing gate ===
{feedback}
=== End of feedback ===

Read your prior commits to understand what you already did, address the
feedback above, run the relevant tests, and commit. Follow the SDLC in
CLAUDE.md. Do NOT call any Linear/MCP tools — the Night Manager handles
all Linear updates.\
"""


def _build_agent_env(issue: IssueState) -> dict[str, str]:
    """Build the env dict shared by spawn_agent and resume_agent."""
    ports = issue.ports
    assert ports is not None
    env_vars = {
        "PG_PORT": str(ports.pg),
        "E2E_FASTAPI_PORT": str(ports.api),
        "E2E_WEB_PORT": str(ports.web),
        "E2E_API_URL": f"http://127.0.0.1:{ports.api}",
        "E2E_BASE_URL": f"http://localhost:{ports.web}",
        "DATABASE_URL": f"postgresql+psycopg://layer1:layer1@localhost:{ports.pg}/layer1_test",
    }
    return {**os.environ, **env_vars}


async def teardown_e2e_stack(issue: IssueState) -> None:
    """Tear down the e2e stack (FastAPI + Next.js) after an agent exits.

    Runs e2e-down.sh with the issue's port env vars so the lsof-based
    fallback in that script finds the right listeners even when pidfiles
    are missing. Idempotent — safe to call when the stack is already down.
    """
    ports = issue.ports
    if not ports:
        return

    worktree = issue.worktree
    cwd = worktree if worktree and Path(worktree).exists() else str(REPO_ROOT)

    env = _build_agent_env(issue)

    try:
        proc = await asyncio.create_subprocess_exec(
            "./scripts/e2e-down.sh",
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=60,
            )
        except asyncio.TimeoutError:
            proc.kill()
            log.warning("e2e-down timed out for %s — killed", issue.identifier)
            return

        if proc.returncode == 0:
            log.info(
                "Tore down e2e stack for %s (ports PG=%d API=%d WEB=%d)",
                issue.identifier, ports.pg, ports.api, ports.web,
            )
        else:
            combined = (stdout + stderr).decode("utf-8", errors="replace")
            log.warning(
                "e2e-down for %s exited %d: %s",
                issue.identifier, proc.returncode, combined[:500],
            )
    except OSError as exc:
        log.warning("Failed to run e2e-down for %s: %s", issue.identifier, exc)


def _snapshot_log_offset(log_file: str) -> int:
    """Capture the log file size BEFORE a new attempt writes anything.

    The error_detail fallback in `_run_agent_lifecycle` reads from this
    offset to EOF, so historical events from prior attempts in the same
    append-only log file can no longer false-positive the rate-limit
    classifier (see ABS-146).
    """
    if not log_file:
        return 0
    p = Path(log_file)
    try:
        return p.stat().st_size if p.exists() else 0
    except OSError:
        return 0


async def setup_worktree(issue: IssueState, state: NMState) -> Path:
    """Create a git worktree for the issue off dev."""
    worktree_name = f"nm-{issue.identifier.lower()}"
    worktree_path = WORKTREE_ROOT / worktree_name
    issue.worktree = str(worktree_path)

    if worktree_path.exists():
        log.info("Worktree %s already exists, reusing", worktree_path)
        return worktree_path

    proc = await asyncio.create_subprocess_exec(
        "git", "worktree", "add", str(worktree_path),
        "-b", issue.branch, "dev",
        cwd=str(REPO_ROOT),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"Failed to create worktree for {issue.identifier}: "
            f"{_combine_streams(stdout, stderr)}"
        )
    log.info("Created worktree %s on branch %s", worktree_path, issue.branch)

    await _setup_worktree_deps(worktree_path)
    return worktree_path


async def _setup_worktree_deps(worktree_path: Path) -> None:
    """Run dev-setup.sh --skip-db + pip install advisor extras + npm install."""
    steps = [
        (["./scripts/dev-setup.sh", "--skip-db"], "dev-setup"),
        ([".venv/bin/pip", "install", "-e", ".[advisor]"], "pip install advisor"),
        (["bash", "-c", "cd web && npm install"], "npm install"),
    ]
    for cmd, label in steps:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(worktree_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            log.warning(
                "Worktree setup step '%s' failed (rc=%d): %s",
                label, proc.returncode, _combine_streams(stdout, stderr)[:500],
            )
        else:
            log.info("Worktree setup step '%s' completed", label)


async def spawn_agent(
    issue: IssueState,
    config: NMConfig,
    description: str | None = None,
) -> asyncio.subprocess.Process:
    """Launch a claude -p subprocess for the issue."""
    # ABS-150: any re-spawn (attempts > 0) means the prior session_id was
    # consumed by Claude Code — whether NM observed completion (and set
    # completed_at) or missed it due to a kernel panic / SIGKILL before
    # state.save(). Reusing a consumed session_id is rejected by the CLI
    # with "Session ID already in use" before any API call. Allocate a
    # fresh UUID on every re-spawn; ABS-145's resume_agent gate handled
    # only the completed_at-set case.
    if issue.attempts > 0 and issue.session_id:
        log.info(
            "Re-spawn for %s: dropping consumed session_id %s",
            issue.identifier, issue.session_id[:8],
        )
        issue.session_id = ""
    session_id = issue.session_id or str(uuid.uuid4())
    issue.session_id = session_id

    worktree_path = issue.worktree
    ports = issue.ports
    assert ports is not None

    prompt = DEV_AGENT_PROMPT_TEMPLATE.format(
        identifier=issue.identifier,
        title=issue.title,
        description=description or "See the Linear issue for details.",
        worktree_path=worktree_path,
        branch=issue.branch,
        pg_port=ports.pg,
        api_port=ports.api,
        web_port=ports.web,
    )

    env = _build_agent_env(issue)

    model = issue.agent_model or config.agent_model
    effort = issue.agent_effort or config.agent_effort

    cmd = [
        "claude",
        "-p",
        "--verbose",
        "--output-format", "stream-json",
        "--permission-mode", "acceptEdits",
        "--allowedTools", ALLOWED_TOOLS,
        "--append-system-prompt", DEV_AGENT_SYSTEM_PROMPT,
        "--session-id", session_id,
        # ABS-149: surface each NM agent in the Claude mobile app's
        # session list. Name = nm-<issue identifier> so the operator can
        # tell parallel agents apart on a phone.
        "--remote-control", f"nm-{issue.identifier}",
        "--model", model,
        "--max-budget-usd", str(config.agent_token_limit),
    ]
    if effort != "high":
        cmd.extend(["--effort", effort])
    cmd.append(prompt)

    log_path = Path(issue.log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    issue.attempt_log_offset = _snapshot_log_offset(issue.log_file)
    log_fh = open(log_path, "a")

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=worktree_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=log_fh,
        env=env,
        limit=10 * 1024 * 1024,  # 10 MB — stream-json lines can be large
    )
    issue.pid = proc.pid or 0
    issue.mark_started()
    log.info(
        "Spawned agent for %s (pid=%d, session=%s, model=%s, effort=%s)",
        issue.identifier, issue.pid, session_id, model, effort,
    )
    return proc


async def resume_agent(
    issue: IssueState,
    config: NMConfig,
    feedback: str,
) -> asyncio.subprocess.Process:
    """Send feedback to the agent for an already-spawned issue.

    Two paths:

    * **Prior session was consumed** (`issue.completed_at` is set) —
      Claude Code v2.x marks sessions consumed once they emit
      `terminal_reason: completed`. Re-using that session_id via
      `--resume` is rejected before any API call (see ABS-145).
      We spawn a fresh session with a new UUID and pass the feedback
      as the initial prompt; the worktree's git log is the source of
      truth for prior work the new session needs to read.

    * **Prior session is mid-flight** (e.g., watchdog kill, kernel
      panic before completed_at was set) — `--resume <id>` still
      works, so we keep the cheaper cache-reuse path for that case.
    """
    assert issue.session_id, f"No session_id for {issue.identifier}"

    if issue.completed_at is not None:
        return await _spawn_continuation(issue, config, feedback)

    env = _build_agent_env(issue)

    model = issue.agent_model or config.agent_model
    effort = issue.agent_effort or config.agent_effort

    cmd = [
        "claude",
        "-p",
        "--verbose",
        "--output-format", "stream-json",
        "--permission-mode", "acceptEdits",
        "--allowedTools", ALLOWED_TOOLS,
        "--resume", issue.session_id,
        # ABS-149: same nm-<identifier> label as the original spawn so
        # the mobile app threads this continuation onto the same session.
        "--remote-control", f"nm-{issue.identifier}",
        "--model", model,
        "--max-budget-usd", str(config.agent_token_limit),
    ]
    if effort != "high":
        cmd.extend(["--effort", effort])
    cmd.append(feedback)

    log_path = Path(issue.log_file)
    issue.attempt_log_offset = _snapshot_log_offset(issue.log_file)
    log_fh = open(log_path, "a")

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=issue.worktree,
        stdout=asyncio.subprocess.PIPE,
        stderr=log_fh,
        env=env,
        limit=10 * 1024 * 1024,  # 10 MB — stream-json lines can be large
    )
    issue.pid = proc.pid or 0
    issue.attempts += 1
    log.info(
        "Resumed agent for %s (pid=%d, attempt=%d, mode=--resume)",
        issue.identifier, issue.pid, issue.attempts,
    )
    return proc


async def _spawn_continuation(
    issue: IssueState,
    config: NMConfig,
    feedback: str,
) -> asyncio.subprocess.Process:
    """Spawn a fresh Claude Code session to handle feedback on a completed issue.

    Used when the prior session was consumed (`terminal_reason: completed`)
    so `--resume` would be rejected by the CLI. Generates a new session_id,
    persists it onto the issue, and uses a continuation prompt that points
    the new agent at the worktree's existing commits.
    """
    new_session_id = str(uuid.uuid4())
    old_session_id = issue.session_id
    issue.session_id = new_session_id

    ports = issue.ports
    assert ports is not None

    prompt = CONTINUATION_PROMPT_TEMPLATE.format(
        identifier=issue.identifier,
        title=issue.title,
        worktree_path=issue.worktree,
        branch=issue.branch,
        pg_port=ports.pg,
        api_port=ports.api,
        web_port=ports.web,
        feedback=feedback,
    )

    env = _build_agent_env(issue)

    model = issue.agent_model or config.agent_model
    effort = issue.agent_effort or config.agent_effort

    cmd = [
        "claude",
        "-p",
        "--verbose",
        "--output-format", "stream-json",
        "--permission-mode", "acceptEdits",
        "--allowedTools", ALLOWED_TOOLS,
        "--append-system-prompt", DEV_AGENT_SYSTEM_PROMPT,
        "--session-id", new_session_id,
        # ABS-149: continuation reuses the same nm-<identifier> label so
        # the mobile app sees a single thread per issue across spawns.
        "--remote-control", f"nm-{issue.identifier}",
        "--model", model,
        "--max-budget-usd", str(config.agent_token_limit),
    ]
    if effort != "high":
        cmd.extend(["--effort", effort])
    cmd.append(prompt)

    log_path = Path(issue.log_file)
    issue.attempt_log_offset = _snapshot_log_offset(issue.log_file)
    log_fh = open(log_path, "a")

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=issue.worktree,
        stdout=asyncio.subprocess.PIPE,
        stderr=log_fh,
        env=env,
        limit=10 * 1024 * 1024,
    )
    issue.pid = proc.pid or 0
    issue.attempts += 1
    # The issue is no longer in a terminal state — it is being worked again.
    # Clear completed_at so a subsequent resume_agent invocation will see
    # this as mid-flight rather than another consumed-session case.
    issue.completed_at = None
    log.info(
        "Spawned continuation for %s (pid=%d, attempt=%d, old_session=%s, new_session=%s)",
        issue.identifier, issue.pid, issue.attempts,
        old_session_id[:8] if old_session_id else "(none)",
        new_session_id[:8],
    )
    return proc


def _truncate(s: str, limit: int = 500) -> str:
    """Truncate a string to `limit` chars, suffixing an ellipsis when cut."""
    if len(s) <= limit:
        return s
    return s[:limit] + "..."


def _log_kill_forensics(
    issue: IssueState,
    elapsed_secs: float,
    last_assistant_text: str,
    last_tool_use: dict[str, Any] | None,
) -> None:
    """Emit a forensics snapshot to the main NM log before SIGTERM.

    Previously the only log line was "Agent X stuck for N minutes, killing",
    which left post-mortems re-parsing the per-issue jsonl. We now surface
    the last assistant text + the last tool_use (name + input) so the
    operator can immediately see what the agent was doing when it stalled.
    """
    elapsed_min = elapsed_secs / 60.0
    tool_summary = "(none)"
    if last_tool_use is not None:
        name = last_tool_use.get("name", "?")
        raw_input = last_tool_use.get("input")
        try:
            input_str = json.dumps(raw_input, ensure_ascii=False) if raw_input is not None else ""
        except (TypeError, ValueError):
            input_str = str(raw_input)
        tool_summary = f"{name} input={_truncate(input_str, 500)}"

    log.warning(
        "Agent %s stall forensics — elapsed=%.1fmin last_tool_use=%s last_assistant_text=%s",
        issue.identifier,
        elapsed_min,
        tool_summary,
        _truncate(last_assistant_text or "(none)", 500),
    )


async def monitor_agent(
    proc: asyncio.subprocess.Process,
    issue: IssueState,
    timeout_minutes: int = STUCK_TIMEOUT_MINUTES,
    bash_tool_budget_minutes: int = BASH_TOOL_BUDGET_MINUTES,
    post_success_idle_minutes: int = POST_SUCCESS_IDLE_MINUTES,
) -> tuple[int, str]:
    """
    Stream agent output, detect stuck state, return (exit_code, final_output).

    Writes each JSON event to the issue's log file and tracks the last
    assistant message as the final output. When the last stream-json event
    is an unterminated `tool_use` for `Bash`, the watchdog extends grace
    to `bash_tool_budget_minutes` for that call — a long-running `make e2e`
    legitimately emits no stdout for ~9 min and was misread as "stuck" in
    ABS-121.

    Post-success idle watchdog (ABS-159): if the most recent event is a
    `result` block with `subtype: "success"` / `is_error: false`, the
    agent's real work is finished but the wrapper is still running. An
    orphaned foreground child (e.g. `npx playwright show-report` serving
    on :9323) can hold the wrapper open indefinitely. After
    `post_success_idle_minutes` of post-success quiet, SIGTERM the
    wrapper so NM can enter the merge gate; SIGKILL after a 10s grace.
    """
    final_output = ""
    loop = asyncio.get_event_loop()
    last_activity = loop.time()
    # Per-tool tracking: when an assistant message contains a tool_use, we
    # remember the (id, name, input, start_time) of the most recent one.
    # When a `user` message with a tool_result arrives whose tool_use_id
    # matches, we mark that tool_use as terminated and clear the pending
    # state — so the watchdog's "extended Bash budget" applies only while
    # a Bash call is actually outstanding.
    pending_tool: dict[str, Any] | None = None  # {id, name, input, started_at}
    # Post-success tracking: timestamp of the most recent `result` event
    # with subtype=success / is_error=false. None until that event arrives.
    success_result_at: float | None = None
    log_path = Path(issue.log_file)

    assert proc.stdout is not None

    async def _read_stream() -> str:
        nonlocal last_activity, final_output, pending_tool, success_result_at
        async for raw_line in proc.stdout:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue

            now = loop.time()
            last_activity = now

            with open(log_path, "a") as f:
                f.write(line + "\n")

            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            etype = event.get("type")
            if etype == "assistant" and "message" in event:
                msg = event["message"]
                if isinstance(msg, dict):
                    for block in msg.get("content", []):
                        if not isinstance(block, dict):
                            continue
                        btype = block.get("type")
                        if btype == "text":
                            final_output = block.get("text", final_output)
                        elif btype == "tool_use":
                            pending_tool = {
                                "id": block.get("id", ""),
                                "name": block.get("name", ""),
                                "input": block.get("input"),
                                "started_at": now,
                            }
                elif isinstance(msg, str):
                    final_output = msg
            elif etype == "user" and "message" in event:
                # tool_result terminates a pending tool_use
                msg = event["message"]
                if isinstance(msg, dict):
                    for block in msg.get("content", []):
                        if (
                            isinstance(block, dict)
                            and block.get("type") == "tool_result"
                            and pending_tool is not None
                            and block.get("tool_use_id") == pending_tool.get("id")
                        ):
                            pending_tool = None
                            break
            elif etype == "result":
                # Claude Code emits a single terminal `result` block when the
                # agent has finished its turn. is_error=false / subtype=success
                # means the agent's work succeeded and stdout from here on is
                # just wrapper drain. If the wrapper doesn't exit promptly,
                # an orphaned foreground child is holding it open — the
                # post-success idle watchdog reaps it.
                is_error = bool(event.get("is_error", False))
                subtype = event.get("subtype", "")
                if not is_error or subtype == "success":
                    success_result_at = now

        return final_output

    async def _watchdog() -> None:
        nonlocal last_activity
        default_timeout_secs = timeout_minutes * 60
        bash_budget_secs = bash_tool_budget_minutes * 60
        post_success_idle_secs = post_success_idle_minutes * 60
        while proc.returncode is None:
            await asyncio.sleep(30)
            now = loop.time()
            elapsed = now - last_activity

            # Post-success idle check (ABS-159): if the agent already wrote
            # a success `result` block but the wrapper is still alive, kill
            # it. This branch is checked BEFORE the standard / Bash-budget
            # logic because a hung wrapper after success has no pending
            # tool_use we care about — the agent thinks it's done.
            if success_result_at is not None:
                post_success_elapsed = now - success_result_at
                if post_success_elapsed > post_success_idle_secs:
                    log.warning(
                        "Agent %s wrapper idle for %.1fm after success result "
                        "— killing wrapper",
                        issue.identifier, post_success_elapsed / 60.0,
                    )
                    try:
                        proc.send_signal(signal.SIGTERM)
                        # 10s grace before SIGKILL (per ABS-159 AC).
                        await asyncio.wait_for(proc.wait(), timeout=10)
                    except (asyncio.TimeoutError, ProcessLookupError):
                        proc.kill()
                    return
                # Still within the post-success grace window — skip the
                # other branches; the agent is in the wrapper-drain phase
                # and shouldn't be penalised by the stdout-silence logic
                # (last_activity stopped advancing once the result landed).
                continue

            # Bash-aware grace: if an unterminated Bash tool_use is in
            # flight, measure budget against the tool's own start time
            # rather than last_activity (which equals the tool_use event
            # itself — and won't advance until the Bash call completes).
            effective_timeout = default_timeout_secs
            if pending_tool is not None and pending_tool.get("name") == "Bash":
                tool_elapsed = now - pending_tool["started_at"]
                if tool_elapsed <= bash_budget_secs:
                    # Bash call still within its extended budget — let it run.
                    continue
                # Exceeded Bash budget — treat as stuck. Surface that the
                # kill was budget-driven, not stdout-silence-driven.
                effective_timeout = bash_budget_secs
                elapsed = tool_elapsed

            if elapsed > effective_timeout:
                _log_kill_forensics(
                    issue,
                    elapsed_secs=elapsed,
                    last_assistant_text=final_output,
                    last_tool_use=pending_tool,
                )
                log.warning(
                    "Agent %s stuck for %.1f minutes, killing",
                    issue.identifier, elapsed / 60.0,
                )
                try:
                    proc.send_signal(signal.SIGTERM)
                    await asyncio.wait_for(proc.wait(), timeout=15)
                except (asyncio.TimeoutError, ProcessLookupError):
                    proc.kill()
                return

    reader_task = asyncio.create_task(_read_stream())
    watchdog_task = asyncio.create_task(_watchdog())

    try:
        await proc.wait()
        watchdog_task.cancel()
        final_output = await reader_task
    except asyncio.CancelledError:
        proc.kill()
        raise

    exit_code = proc.returncode or 0
    log.info(
        "Agent %s finished (exit=%d, output_len=%d)",
        issue.identifier, exit_code, len(final_output),
    )
    return exit_code, final_output


async def kill_agent(issue: IssueState) -> None:
    """Kill an agent by PID if it's still running."""
    if issue.pid <= 0:
        return
    try:
        os.kill(issue.pid, signal.SIGTERM)
        log.info("Sent SIGTERM to agent %s (pid=%d)", issue.identifier, issue.pid)
    except ProcessLookupError:
        pass


def is_pid_alive(pid: int) -> bool:
    """Return True if `pid` is a live process owned by this user.

    Uses signal 0 (the "null" signal) — sends nothing, but POSIX returns
    EPERM if the pid exists but is owned by someone else and ESRCH if it
    doesn't exist at all. We treat both EPERM (process exists, foreign
    owner — extremely unlikely for NM-spawned agents) and "no error" as
    alive; ESRCH (ProcessLookupError) is the only "dead" signal.

    After a kernel panic every NM-recorded PID is stale, so this is the
    fast precondition for the resume path — we mustn't treat a dead PID
    as a live agent we're "waiting on".
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # PID exists but is owned by another user — still alive in the
        # OS sense. Shouldn't happen for NM but be conservative.
        return True
    return True


async def cleanup_worktree(issue: IssueState) -> None:
    """Remove the git worktree for a completed/failed issue."""
    worktree_path = issue.worktree
    if not worktree_path:
        return
    proc = await asyncio.create_subprocess_exec(
        "git", "worktree", "remove", "--force", worktree_path,
        cwd=str(REPO_ROOT),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()
    if proc.returncode == 0:
        log.info("Removed worktree %s", worktree_path)
    else:
        log.warning("Failed to remove worktree %s (may need manual cleanup)", worktree_path)


async def _get_ppid(pid: int) -> int | None:
    """Return the parent PID of *pid*, or None on failure."""
    proc = await asyncio.create_subprocess_exec(
        "ps", "-o", "ppid=", "-p", str(pid),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await proc.communicate()
    try:
        return int(stdout.decode().strip())
    except (ValueError, AttributeError):
        return None


NM_PORT_RANGES = [
    (PORT_BASE_PG, PORT_BASE_PG + 50),
    (PORT_BASE_API, PORT_BASE_API + 50),
    (PORT_BASE_WEB, PORT_BASE_WEB + 50),
]


def _port_in_nm_range(port: int) -> bool:
    return any(lo <= port < hi for lo, hi in NM_PORT_RANGES)


async def reap_orphan_listeners() -> list[tuple[int, int]]:
    """Kill orphaned TCP listeners on NM port ranges whose PPID is 1.

    Intended to run at NM startup (fresh run and resume) to clean up
    stacks left behind by prior NM runs that crashed without going
    through the agent-exit teardown path. Emits a WARNING per kill.

    Returns a list of (pid, port) tuples that were reaped.
    """
    proc = await asyncio.create_subprocess_exec(
        "lsof", "-iTCP", "-sTCP:LISTEN", "-nP",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        return []

    candidates: dict[int, int] = {}  # pid -> port
    for line in stdout.decode("utf-8", errors="replace").splitlines()[1:]:
        parts = line.split()
        if len(parts) < 9:
            continue
        try:
            pid = int(parts[1])
        except ValueError:
            continue
        name = parts[8]
        if ":" not in name:
            continue
        try:
            port = int(name.rsplit(":", 1)[1])
        except (ValueError, IndexError):
            continue
        if _port_in_nm_range(port):
            candidates[pid] = port

    if not candidates:
        return []

    reaped: list[tuple[int, int]] = []
    for pid, port in candidates.items():
        ppid = await _get_ppid(pid)
        if ppid != 1:
            continue
        log.warning(
            "Orphan reaper: killing PID %d listening on port %d (PPID=1)",
            pid, port,
        )
        try:
            os.kill(pid, signal.SIGTERM)
            reaped.append((pid, port))
        except ProcessLookupError:
            pass

    if reaped:
        log.warning(
            "Orphan reaper: reaped %d orphan listener(s): %s",
            len(reaped),
            [(pid, port) for pid, port in reaped],
        )
    return reaped


# ABS-160: integration target for rebase-on-resume. The full project
# integrates new work into `dev` (not `main`); see CLAUDE.md and
# docs/BRANCHING_STRATEGY.md. Centralised here so test fixtures can
# patch a single symbol when validating the conflict path.
REBASE_TARGET_BRANCH = "origin/dev"


async def rebase_worktree_onto_dev(
    issue: IssueState,
) -> tuple[bool, str]:
    """Fetch + rebase the issue's worktree onto current `origin/dev`.

    Returns (success, message). Used by the resume path to eliminate
    the "stale-fork-of-dev makes my e2e fail on a since-fixed test"
    trigger documented in ABS-160 (nm-20260526-153728). Without this,
    a worktree forked an hour ago can fail on a since-merged spec fix,
    the agent "fixes" the spec under its own ticket prefix, and the
    diff-vs-areas gate has to do all the work alone.

    On conflicts the rebase is aborted and we return (False, msg).
    The caller is responsible for marking the issue blocked / removing
    it from the resume target list — surfacing it for manual handling
    is preferable to silently re-spawning into stale code.

    No-op (returns success) when `issue.worktree` is empty or the
    directory doesn't exist — the resume path can still hit fresh
    issues that never got a worktree allocated.
    """
    worktree = issue.worktree
    if not worktree:
        return True, "No worktree to rebase (issue never started)."

    worktree_path = Path(worktree)
    if not worktree_path.exists():
        # The worktree was hand-removed (e.g. by post-merge cleanup)
        # — nothing to rebase. The downstream spawn will recreate it.
        return True, f"Worktree {worktree} no longer exists; nothing to rebase."

    # 1. Fetch origin so origin/dev reflects today's tip.
    fetch_proc = await asyncio.create_subprocess_exec(
        "git", "-C", worktree, "fetch", "origin",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    fetch_out, fetch_err = await fetch_proc.communicate()
    if fetch_proc.returncode != 0:
        err = _combine_streams(fetch_out, fetch_err)
        log.error(
            "ERROR: rebase-on-resume fetch failed for %s in %s: %s",
            issue.identifier, worktree, err,
        )
        return False, f"git fetch failed in {worktree}: {err}"

    # 2. Rebase onto current origin/dev.
    rebase_proc = await asyncio.create_subprocess_exec(
        "git", "-C", worktree, "rebase", REBASE_TARGET_BRANCH,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    rebase_out, rebase_err = await rebase_proc.communicate()
    if rebase_proc.returncode == 0:
        log.info(
            "Rebased %s worktree (%s) onto %s before re-spawn",
            issue.identifier, worktree, REBASE_TARGET_BRANCH,
        )
        return True, f"Rebased {worktree} onto {REBASE_TARGET_BRANCH}"

    # 3. Conflict path — abort and surface a clear error. Don't leave
    # the worktree in a half-rebased state; the post-merge cleanup or
    # a follow-up manual session needs a clean tree to work with.
    conflict_detail = _combine_streams(rebase_out, rebase_err)
    abort_proc = await asyncio.create_subprocess_exec(
        "git", "-C", worktree, "rebase", "--abort",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await abort_proc.communicate()

    log.error(
        "ERROR: rebase-on-resume for %s failed with conflicts in %s. "
        "Continuation NOT spawned — issue needs manual attention.\n"
        "Conflict detail (truncated):\n%s",
        issue.identifier, worktree, conflict_detail[:1000],
    )
    msg = (
        f"Rebase onto {REBASE_TARGET_BRANCH} conflicted in {worktree}. "
        f"Aborted to keep the worktree clean. Resolve manually before "
        f"re-running the night manager. Conflict detail:\n{conflict_detail[:1000]}"
    )
    return False, msg
