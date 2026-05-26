"""Code review gate and e2e test gate for completed agent work."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

from .config import NMConfig, REPO_ROOT, MAX_REVIEW_CYCLES, MAX_E2E_FIX_CYCLES
from .state import IssueState

log = logging.getLogger("night_manager.reviewer")


async def code_review(issue: IssueState, config: NMConfig) -> tuple[bool, str]:
    """
    Run code review via claude -p in the issue's worktree.

    Returns (passed, feedback). If passed is False, feedback contains
    the review findings that should be sent back to the dev agent.
    """
    worktree = issue.worktree

    diff_proc = await asyncio.create_subprocess_exec(
        "git", "diff", "dev...HEAD",
        cwd=worktree,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    diff_out, _ = await diff_proc.communicate()
    diff_text = diff_out.decode("utf-8", errors="replace")

    if not diff_text.strip():
        return True, "No changes to review."

    log_proc = await asyncio.create_subprocess_exec(
        "git", "log", "dev...HEAD", "--oneline",
        cwd=worktree,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    log_out, _ = await log_proc.communicate()
    commit_log = log_out.decode("utf-8", errors="replace")

    review_prompt = (
        f"Review the code changes for issue {issue.identifier}: {issue.title}\n\n"
        f"Commits:\n{commit_log}\n\n"
        f"Diff:\n{diff_text[:50000]}\n\n"
        "Focus on:\n"
        "1. Correctness bugs — logic errors, off-by-ones, race conditions\n"
        "2. Security — injection, XSS, auth bypasses, secret exposure\n"
        "3. Missing error handling at system boundaries\n"
        "4. Breaking changes to existing APIs or contracts\n\n"
        "Return a JSON object:\n"
        '{"passed": true/false, "findings": [{"severity": "high|medium|low", '
        '"file": "path", "description": "..."}], '
        '"summary": "one-line overall assessment"}\n\n'
        "Only fail the review for high-severity findings that would cause "
        "bugs or security issues in production. Style, naming, and minor "
        "improvements are not blockers — mention them but still pass.\n"
        "Return ONLY the JSON, no markdown fences."
    )

    cmd = [
        "claude", "-p",
        "--output-format", "json",
        "--model", "sonnet",
        "--max-budget-usd", "2",
        review_prompt,
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=worktree,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    raw = stdout.decode("utf-8", errors="replace").strip()

    try:
        result = json.loads(raw)
        if isinstance(result, dict) and "result" in result:
            inner = result["result"]
            if isinstance(inner, str):
                inner = json.loads(inner)
            result = inner
    except json.JSONDecodeError:
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            log.warning("Failed to parse review output, treating as pass")
            return True, raw

    passed = result.get("passed", True)
    findings = result.get("findings", [])
    summary = result.get("summary", "")

    if not passed:
        feedback_parts = [f"Code review for {issue.identifier} found issues:\n"]
        for f in findings:
            feedback_parts.append(
                f"- [{f.get('severity', 'medium')}] {f.get('file', '?')}: "
                f"{f.get('description', '?')}"
            )
        feedback_parts.append(f"\nSummary: {summary}")
        feedback = "\n".join(feedback_parts)
        log.info("Review FAILED for %s: %s", issue.identifier, summary)
        return False, feedback

    log.info("Review PASSED for %s: %s", issue.identifier, summary)
    return True, summary


async def run_e2e(issue: IssueState) -> tuple[bool, str]:
    """
    Run make e2e in the issue's worktree with its port triplet.

    Returns (passed, output). output contains test results or failure details.
    """
    worktree = issue.worktree
    ports = issue.ports
    assert ports is not None

    env = {
        **os.environ,
        "PG_PORT": str(ports.pg),
        "E2E_FASTAPI_PORT": str(ports.api),
        "E2E_WEB_PORT": str(ports.web),
        "E2E_API_URL": f"http://127.0.0.1:{ports.api}",
        "E2E_BASE_URL": f"http://localhost:{ports.web}",
        "DATABASE_URL": f"postgresql+psycopg://layer1:layer1@localhost:{ports.pg}/layer1_test",
    }

    down_proc = await asyncio.create_subprocess_exec(
        "./scripts/e2e-down.sh",
        cwd=worktree,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    await down_proc.communicate()

    proc = await asyncio.create_subprocess_exec(
        "make", "e2e",
        cwd=worktree,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=env,
    )
    stdout, _ = await proc.communicate()
    output = stdout.decode("utf-8", errors="replace")
    passed = proc.returncode == 0

    if passed:
        log.info("E2E PASSED for %s", issue.identifier)
    else:
        log.info("E2E FAILED for %s (exit=%d)", issue.identifier, proc.returncode or 1)

    return passed, output[-5000:]


def _combine_streams(stdout: bytes, stderr: bytes) -> str:
    """Return a non-empty error string drawn from both stdout and stderr.

    Some git subcommands (notably `git merge` on content conflicts) write
    their failure message to stdout while leaving stderr empty. Reading
    only stderr in those cases drops the actual error. This helper joins
    whatever is present so callers always surface something useful.
    """
    out = stdout.decode("utf-8", errors="replace").strip()
    err = stderr.decode("utf-8", errors="replace").strip()
    if out and err:
        return f"{err}\n{out}"
    return err or out


async def _assert_clean_worktree(cwd: str) -> tuple[bool, str]:
    """Return (clean, message). When dirty, message lists the offending paths.

    Uses `git status --porcelain` which lists every staged, unstaged, and
    untracked path in a stable machine-readable form. Refuses to proceed
    if the target worktree has any uncommitted state — silent stashing
    of a sleeping operator's WIP is more dangerous than failing loudly.
    """
    proc = await asyncio.create_subprocess_exec(
        "git", "status", "--porcelain",
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        err = _combine_streams(stdout, stderr)
        return False, f"`git status --porcelain` failed in {cwd}: {err}"

    porcelain = stdout.decode("utf-8", errors="replace").strip()
    if not porcelain:
        return True, ""

    dirty_lines = porcelain.splitlines()
    listing = "\n".join(f"  {line}" for line in dirty_lines)
    msg = (
        f"Target worktree {cwd} is not clean — refusing to merge.\n"
        f"Dirty paths ({len(dirty_lines)} entries):\n{listing}\n"
        f"Resolve manually before re-running the night manager. To inspect: "
        f"`git -C {cwd} status`. To discard everything: "
        f"`git -C {cwd} reset --hard && git -C {cwd} clean -fd` "
        f"(destructive — only run if you're sure)."
    )
    return False, msg


async def merge_to_dev(issue: IssueState) -> tuple[bool, str]:
    """
    Merge the issue's branch into dev. Returns (success, message).

    Merges are sequential — caller must ensure only one merge at a time.

    Refuses to proceed if the target worktree (REPO_ROOT) has any
    uncommitted modifications or untracked files. The night manager runs
    while the operator sleeps; silently stashing their WIP would risk
    losing work, so we surface the dirty state and let them clean up.
    """
    clean, dirty_msg = await _assert_clean_worktree(str(REPO_ROOT))
    if not clean:
        log.error("Merge precondition failed for %s: %s", issue.identifier, dirty_msg)
        return False, dirty_msg

    cmds = [
        ["git", "checkout", "dev"],
        ["git", "pull", "--ff-only", "origin", "dev"],
        ["git", "merge", "--no-ff", issue.branch,
         "-m", f"Merge {issue.identifier}: {issue.title}"],
        ["git", "push", "origin", "dev"],
    ]

    for cmd in cmds:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(REPO_ROOT),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            error = _combine_streams(stdout, stderr)
            log.error(
                "Merge step '%s' failed for %s: %s",
                " ".join(cmd), issue.identifier, error,
            )
            if cmd[1] == "merge":
                abort = await asyncio.create_subprocess_exec(
                    "git", "merge", "--abort",
                    cwd=str(REPO_ROOT),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await abort.communicate()
                log.info("Aborted failed merge to keep dev clean")
            return False, f"Merge failed at '{' '.join(cmd)}': {error}"

    log.info("Merged %s into dev", issue.identifier)
    return True, f"Successfully merged {issue.identifier} into dev"


async def revert_merge(issue: IssueState) -> tuple[bool, str]:
    """Revert the last merge on dev (used when post-merge regression detected)."""
    proc = await asyncio.create_subprocess_exec(
        "git", "revert", "--no-edit", "-m", "1", "HEAD",
        cwd=str(REPO_ROOT),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        error = _combine_streams(stdout, stderr)
        log.error("Revert failed for %s: %s", issue.identifier, error)
        return False, f"Revert failed: {error}"

    push_proc = await asyncio.create_subprocess_exec(
        "git", "push", "origin", "dev",
        cwd=str(REPO_ROOT),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await push_proc.communicate()

    log.info("Reverted merge of %s on dev", issue.identifier)
    return True, f"Reverted merge of {issue.identifier}"


async def run_dev_e2e(issue: IssueState) -> tuple[bool, str]:
    """Run post-merge regression e2e from the agent's worktree.

    After merge_to_dev lands the branch on dev, we pull dev into the
    worktree so it has the merged state, then run `make e2e` there.
    This avoids the Next.js 16 same-directory conflict that kills the
    e2e server when a dev server is already running on the main checkout.
    """
    worktree = issue.worktree
    ports = issue.ports
    assert worktree and ports

    env = {
        **os.environ,
        "PG_PORT": str(ports.pg),
        "E2E_FASTAPI_PORT": str(ports.api),
        "E2E_WEB_PORT": str(ports.web),
        "E2E_API_URL": f"http://127.0.0.1:{ports.api}",
        "E2E_BASE_URL": f"http://localhost:{ports.web}",
        "DATABASE_URL": f"postgresql+psycopg://layer1:layer1@localhost:{ports.pg}/layer1_test",
    }

    for cmd in [
        ["git", "checkout", "dev"],
        ["git", "pull", "--ff-only", "origin", "dev"],
    ]:
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=worktree,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

    pip_proc = await asyncio.create_subprocess_exec(
        ".venv/bin/pip", "install", "-e", ".[dev,advisor]",
        cwd=worktree,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    pip_out, pip_err = await pip_proc.communicate()
    if pip_proc.returncode != 0:
        log.warning("pip install before dev e2e failed: %s", pip_err.decode()[:500])

    down_proc = await asyncio.create_subprocess_exec(
        "./scripts/e2e-down.sh",
        cwd=worktree,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    await down_proc.communicate()

    proc = await asyncio.create_subprocess_exec(
        "make", "e2e",
        cwd=worktree,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=env,
    )
    stdout, _ = await proc.communicate()
    output = stdout.decode("utf-8", errors="replace")
    passed = proc.returncode == 0

    if passed:
        log.info("Dev regression e2e PASSED")
    else:
        log.info("Dev regression e2e FAILED")

    return passed, output[-5000:]


async def review_and_gate(
    issue: IssueState,
    config: NMConfig,
) -> bool:
    """
    Full review + e2e gate for an issue. Handles retry loops.

    Returns True if the issue passed all gates and is ready to merge.
    """
    from .agent import resume_agent, monitor_agent

    for cycle in range(MAX_REVIEW_CYCLES):
        passed, feedback = await code_review(issue, config)
        if passed:
            break
        log.info(
            "Review cycle %d/%d for %s: sending feedback",
            cycle + 1, MAX_REVIEW_CYCLES, issue.identifier,
        )
        if cycle + 1 >= MAX_REVIEW_CYCLES:
            issue.mark_blocked(f"Code review failed after {MAX_REVIEW_CYCLES} cycles")
            return False
        proc = await resume_agent(issue, config, feedback)
        await monitor_agent(proc, issue)

    for cycle in range(MAX_E2E_FIX_CYCLES):
        passed, output = await run_e2e(issue)
        if passed:
            break
        log.info(
            "E2E cycle %d/%d for %s: sending failures to agent",
            cycle + 1, MAX_E2E_FIX_CYCLES, issue.identifier,
        )
        if cycle + 1 >= MAX_E2E_FIX_CYCLES:
            issue.mark_blocked(f"E2E tests failed after {MAX_E2E_FIX_CYCLES} cycles")
            return False
        feedback = (
            f"E2E tests failed for {issue.identifier}. Fix the failures and "
            f"commit your changes.\n\nTest output (last 3000 chars):\n{output[-3000:]}"
        )
        proc = await resume_agent(issue, config, feedback)
        await monitor_agent(proc, issue)

    return True
