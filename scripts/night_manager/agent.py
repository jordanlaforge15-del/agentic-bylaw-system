"""Dev agent lifecycle: spawn, stream, detect completion, resume."""

from __future__ import annotations

import asyncio
import json
import logging
import signal
import uuid
from pathlib import Path
from typing import Any

from .config import ALLOWED_TOOLS, DEV_AGENT_SYSTEM_PROMPT, NMConfig, REPO_ROOT, WORKTREE_ROOT
from .state import IssueState, NMState

log = logging.getLogger("night_manager.agent")


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
            f"{stderr.decode().strip()}"
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
                label, proc.returncode, stderr.decode()[:500],
            )
        else:
            log.info("Worktree setup step '%s' completed", label)


async def spawn_agent(
    issue: IssueState,
    config: NMConfig,
    description: str | None = None,
) -> asyncio.subprocess.Process:
    """Launch a claude -p subprocess for the issue."""
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

    env_vars = {
        "PG_PORT": str(ports.pg),
        "E2E_FASTAPI_PORT": str(ports.api),
        "E2E_WEB_PORT": str(ports.web),
        "E2E_API_URL": f"http://127.0.0.1:{ports.api}",
        "E2E_BASE_URL": f"http://localhost:{ports.web}",
        "DATABASE_URL": f"postgresql+psycopg://layer1:layer1@localhost:{ports.pg}/layer1_test",
    }

    import os
    env = {**os.environ, **env_vars}

    cmd = [
        "claude",
        "-p",
        "--verbose",
        "--output-format", "stream-json",
        "--permission-mode", "acceptEdits",
        "--allowedTools", ALLOWED_TOOLS,
        "--append-system-prompt", DEV_AGENT_SYSTEM_PROMPT,
        "--session-id", session_id,
        "--model", config.model,
        "--max-budget-usd", "10",
        prompt,
    ]

    log_path = Path(issue.log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
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
        "Spawned agent for %s (pid=%d, session=%s)",
        issue.identifier, issue.pid, session_id,
    )
    return proc


async def resume_agent(
    issue: IssueState,
    config: NMConfig,
    feedback: str,
) -> asyncio.subprocess.Process:
    """Resume an agent session with feedback."""
    assert issue.session_id, f"No session_id for {issue.identifier}"
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

    import os
    env = {**os.environ, **env_vars}

    cmd = [
        "claude",
        "-p",
        "--verbose",
        "--output-format", "stream-json",
        "--permission-mode", "acceptEdits",
        "--allowedTools", ALLOWED_TOOLS,
        "--resume", issue.session_id,
        "--model", config.model,
        "--max-budget-usd", "10",
        feedback,
    ]

    log_path = Path(issue.log_file)
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
        "Resumed agent for %s (pid=%d, attempt=%d)",
        issue.identifier, issue.pid, issue.attempts,
    )
    return proc


async def monitor_agent(
    proc: asyncio.subprocess.Process,
    issue: IssueState,
    timeout_minutes: int = 10,
) -> tuple[int, str]:
    """
    Stream agent output, detect stuck state, return (exit_code, final_output).

    Writes each JSON event to the issue's log file and tracks the last
    assistant message as the final output.
    """
    final_output = ""
    last_activity = asyncio.get_event_loop().time()
    log_path = Path(issue.log_file)

    assert proc.stdout is not None

    async def _read_stream() -> str:
        nonlocal last_activity, final_output
        async for raw_line in proc.stdout:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue

            last_activity = asyncio.get_event_loop().time()

            with open(log_path, "a") as f:
                f.write(line + "\n")

            try:
                event = json.loads(line)
                if event.get("type") == "assistant" and "message" in event:
                    msg = event["message"]
                    if isinstance(msg, dict):
                        for block in msg.get("content", []):
                            if isinstance(block, dict) and block.get("type") == "text":
                                final_output = block["text"]
                    elif isinstance(msg, str):
                        final_output = msg
            except (json.JSONDecodeError, KeyError):
                pass

        return final_output

    async def _watchdog() -> None:
        nonlocal last_activity
        timeout_secs = timeout_minutes * 60
        while proc.returncode is None:
            await asyncio.sleep(30)
            elapsed = asyncio.get_event_loop().time() - last_activity
            if elapsed > timeout_secs:
                log.warning(
                    "Agent %s stuck for %d minutes, killing",
                    issue.identifier, timeout_minutes,
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
    import os
    try:
        os.kill(issue.pid, signal.SIGTERM)
        log.info("Sent SIGTERM to agent %s (pid=%d)", issue.identifier, issue.pid)
    except ProcessLookupError:
        pass


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
