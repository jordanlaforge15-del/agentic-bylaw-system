"""Code review gate and e2e test gate for completed agent work."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

from .config import NMConfig, REPO_ROOT, MAX_REVIEW_CYCLES, MAX_E2E_FIX_CYCLES, MAX_REGRESSION_FIX_CYCLES
from .state import IssueState

log = logging.getLogger("night_manager.reviewer")


# Path prefixes that mark a touched file as "test-only" for the
# diff-vs-areas check (see `check_diff_touches_ticket_areas` and
# ABS-160). A diff composed entirely of files under these prefixes,
# against a ticket whose `areas` is non-empty and fully disjoint from
# the touched paths, is treated as an off-topic test fix rather than a
# legitimate deliverable. Keep this list narrow on purpose — anything
# under `scripts/`, `web/app/`, `advisor/`, etc., is production code.
TEST_ONLY_PATH_PREFIXES: tuple[str, ...] = (
    "web/e2e/",
    "tests/",
)


def _is_test_only_path(path: str) -> bool:
    """Return True iff `path` lives entirely under a test directory.

    Used by the diff-vs-areas reviewer gate to detect the ABS-160
    failure mode where an agent commits unrelated test fixes under a
    `[ABS-XX]` prefix while leaving the ticket's actual production
    file untouched.
    """
    return any(path.startswith(prefix) for prefix in TEST_ONLY_PATH_PREFIXES)


def _path_overlaps_area(path: str, area: str) -> bool:
    """Return True iff `path` is under (or equals) `area`.

    `area` is treated as a path prefix tag from the planner's
    `touch_profile["areas"]` (e.g. "Dockerfile.advisor",
    "scripts/night_manager", "pyproject.toml"). A bare filename
    matches an exact-name diff entry; a directory-like tag matches
    any file underneath it. Comparisons are case-sensitive — git
    paths are case-sensitive on Linux/CI even if macOS is forgiving.
    """
    if not area:
        return False
    # Normalise: drop a trailing slash so "scripts/night_manager/" and
    # "scripts/night_manager" behave identically. Don't strip leading
    # `./` — the planner emits canonical project-relative paths.
    norm_area = area.rstrip("/")
    if path == norm_area:
        return True
    # Directory-style overlap: "scripts/night_manager" matches
    # "scripts/night_manager/reviewer.py".
    return path.startswith(norm_area + "/")


def check_diff_touches_ticket_areas(
    issue: IssueState,
    changed_files: list[str],
) -> tuple[bool, str]:
    """Verify the agent's diff actually touches the ticket's declared areas.

    Returns (ok, message). On `ok=False` the caller should fail the
    review and surface `message` back to the user / dev agent.

    The check only fires when ALL of the following are true:

    1. The planner extracted a non-empty `touch_profile["areas"]` for
       this issue. If the list is empty (planner skipped, or the
       ticket genuinely lives across many files), we cannot make a
       confident claim about scope drift — skip the gate.
    2. `changed_files` is non-empty. An empty diff is handled higher
       up in `code_review`.
    3. Every changed file is under a `TEST_ONLY_PATH_PREFIXES` entry
       (e.g. `web/e2e/...` or `tests/...`).
    4. The intersection between `changed_files` and the ticket's
       `areas` is empty — none of the touched files lives under any
       declared area.

    Conditions 3 and 4 together describe the ABS-160 failure mode
    exactly: the agent shipped a test-only fix while the ticket's
    production target (`Dockerfile.advisor`, `pyproject.toml`, ...)
    is unchanged.
    """
    profile = issue.touch_profile or {}
    raw_areas = profile.get("areas") or []
    areas = [str(a).strip() for a in raw_areas if str(a).strip()]
    if not areas:
        # Planner did not produce an areas list — cannot apply the
        # check without risking a false positive on legitimately
        # scoped work whose planner output happened to lack areas.
        return True, ""

    if not changed_files:
        # `code_review` handles the empty-diff case before us, but
        # defend against a regression in caller ordering.
        return True, ""

    test_only = all(_is_test_only_path(p) for p in changed_files)
    if not test_only:
        # At least one production-code file was touched — fall through
        # to the LLM reviewer for normal scrutiny.
        return True, ""

    # Diff is test-only. Check whether any test file genuinely lives
    # under one of the ticket's declared areas (e.g. `tests/advisor`
    # for an `advisor`-area ticket). Disjoint intersection is the
    # ABS-160 fingerprint.
    overlaps = any(
        _path_overlaps_area(path, area)
        for path in changed_files
        for area in areas
    )
    if overlaps:
        return True, ""

    msg = (
        f"Diff touches only test files. Ticket's `areas` ({areas}) are "
        f"unchanged. Either the agent fixed the wrong thing or the "
        f"`areas` field is wrong. Aborting merge.\n\n"
        f"Touched paths:\n" + "\n".join(f"  - {p}" for p in changed_files)
    )
    return False, msg


async def _git_changed_files(worktree: str) -> list[str]:
    """Return `git diff --name-only dev...HEAD` as a list of paths.

    Empty list on git failure rather than raising — the caller
    treats no-files as "skip the areas gate", matching the empty-diff
    branch in `code_review`. Errors are logged for forensics.
    """
    proc = await asyncio.create_subprocess_exec(
        "git", "diff", "--name-only", "dev...HEAD",
        cwd=worktree,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        err = stderr.decode("utf-8", errors="replace").strip()
        log.warning(
            "git diff --name-only failed in %s (rc=%d): %s",
            worktree, proc.returncode, err,
        )
        return []
    raw = stdout.decode("utf-8", errors="replace")
    return [line for line in raw.splitlines() if line.strip()]


async def code_review(issue: IssueState, config: NMConfig) -> tuple[bool, str]:
    """
    Run code review via claude -p in the issue's worktree.

    Returns (passed, feedback). If passed is False, feedback contains
    the review findings that should be sent back to the dev agent.

    Before invoking the LLM reviewer, runs the ABS-160 diff-vs-areas
    sanity check: if the ticket's planner-assigned `areas` is
    non-empty and the diff touches *only* test files disjoint from
    those areas, fail fast with a clear "wrong ticket" error. This
    catches the failure mode where a stale-worktree agent commits
    test fixes under the ticket's `[ABS-XX]` prefix without touching
    the ticket's actual deliverable.
    """
    worktree = issue.worktree

    # ABS-160: cheap, deterministic gate before paying for the LLM.
    # Runs against `git diff --name-only` (path list only) rather than
    # the full diff so it's effectively free.
    changed_files = await _git_changed_files(worktree)
    areas_ok, areas_msg = check_diff_touches_ticket_areas(issue, changed_files)
    if not areas_ok:
        log.warning(
            "Review FAILED (diff-vs-areas) for %s: %s",
            issue.identifier, areas_msg.splitlines()[0],
        )
        return False, areas_msg

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
        "--model", config.reviewer_model,
        "--max-budget-usd", str(config.reviewer_token_limit),
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


def _extract_failing_tests(worktree: str) -> str:
    """Parse Playwright JSON reporter output and return a formatted list of
    failing test names with isolation invocations.

    Reads `web/test-results/results.json` from the worktree.  Falls back
    gracefully (returns empty string) if the file doesn't exist or can't
    be parsed — caller appends to the existing stdout-tail feedback.

    The Playwright JSON reporter schema (reporter "json") writes:
      { "suites": [ { "file": str, "suites": [...], "specs": [...] } ] }
    Each spec has a "title" and "tests" list; each test has "results" with
    a "status" field.  A test is failing if any result has status "failed"
    or "timedOut".
    """
    results_path = Path(worktree) / "web" / "test-results" / "results.json"
    try:
        data = json.loads(results_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.debug("Could not read Playwright results.json from %s: %s", worktree, exc)
        return ""

    failing: list[str] = []

    def _walk_suites(suites: list, file_path: str, describe_stack: list[str]) -> None:
        for suite in suites:
            # Nested describe blocks
            title = suite.get("title") or ""
            child_suites = suite.get("suites") or []
            specs = suite.get("specs") or []
            new_stack = describe_stack + ([title] if title else [])
            _walk_suites(child_suites, file_path, new_stack)
            for spec in specs:
                spec_title = spec.get("title", "")
                tests = spec.get("tests") or []
                is_failing = any(
                    result.get("status") in ("failed", "timedOut")
                    for test in tests
                    for result in (test.get("results") or [])
                )
                if is_failing:
                    parts = new_stack + [spec_title]
                    full_title = " › ".join(p for p in parts if p)
                    failing.append(f"  - {file_path} › {full_title}" if file_path else f"  - {full_title}")

    top_suites = data.get("suites") or []
    for top in top_suites:
        file_path = top.get("file") or top.get("title") or ""
        child_suites = top.get("suites") or []
        specs = top.get("specs") or []
        _walk_suites(child_suites, file_path, [])
        for spec in specs:
            spec_title = spec.get("title", "")
            tests = spec.get("tests") or []
            is_failing = any(
                result.get("status") in ("failed", "timedOut")
                for test in tests
                for result in (test.get("results") or [])
            )
            if is_failing:
                failing.append(f"  - {file_path} › {spec_title}" if file_path else f"  - {spec_title}")

    if not failing:
        return ""

    lines = [
        "Failing tests (run in isolation with: "
        "`cd web && npx playwright test <spec> --grep '<test name>'`):",
    ]
    lines.extend(failing)
    return "\n".join(lines)


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

    tail = output[-5000:]
    if not passed:
        failing_names = _extract_failing_tests(worktree)
        if failing_names:
            tail = failing_names + "\n\n" + tail
    return passed, tail


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

    tail = output[-5000:]
    if not passed:
        failing_names = _extract_failing_tests(worktree)
        if failing_names:
            tail = failing_names + "\n\n" + tail
    return passed, tail


async def restore_worktree_branch(issue: IssueState) -> None:
    """Checkout the issue's feature branch in its worktree.

    After run_dev_e2e the worktree is on dev — this restores it so the
    agent can resume working on the feature branch.
    """
    proc = await asyncio.create_subprocess_exec(
        "git", "checkout", issue.branch,
        cwd=str(issue.worktree),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()


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
