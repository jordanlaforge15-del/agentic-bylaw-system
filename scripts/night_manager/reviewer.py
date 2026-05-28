"""Code review gate and e2e test gate for completed agent work."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from .config import NMConfig, REPO_ROOT, LOGS_DIR, MAX_REVIEW_CYCLES, MAX_E2E_FIX_CYCLES, MAX_REGRESSION_FIX_CYCLES
from .state import IssueState

log = logging.getLogger("night_manager.reviewer")

HEARTBEAT_INTERVAL = 60  # seconds between "still alive" log lines
_FLAKE_CANDIDATE_THRESHOLD = 3  # flakes needed before surfacing flake_candidate=true

# ABS-206: keywords that indicate the agent crashed on an authentication
# failure before producing any commits. Scanned by the empty-diff guard in
# `review_and_gate` so an empty branch is reported with the actual root
# cause ("401 Invalid authentication credentials" etc.) rather than a
# generic "agent produced no commits" line.
AUTH_ERROR_MARKERS: tuple[str, ...] = (
    "401",
    "invalid authentication credentials",
    "authentication failed",
)

# Maps test key → list of booleans (True = test recovered on that solo re-run).
# In-memory only; resets on NM restart, which is fine for the rolling-window intent.
_flake_history: dict[str, list[bool]] = defaultdict(list)


@dataclass
class _FailingTest:
    """Structured representation of a single failing Playwright test."""
    spec_file: str   # relative path under web/ (e.g. e2e/functional/foo.spec.ts)
    test_title: str  # full title including describe stack (e.g. "Suite › test name")

    @property
    def key(self) -> str:
        return f"{self.spec_file}::{self.test_title}"


async def _periodic_heartbeat(label: str, interval: int = HEARTBEAT_INTERVAL) -> None:
    """Emit a 'still alive' log line every `interval` seconds until cancelled.

    Call asyncio.create_task(_periodic_heartbeat(...)) and cancel the task
    when the awaited operation completes.  The interval is intentionally
    shorter than the 90-second silence threshold from ABS-167 so at least
    one heartbeat fires before an operator would suspect a hang.
    """
    start = time.monotonic()
    while True:
        await asyncio.sleep(interval)
        elapsed = int(time.monotonic() - start)
        log.info("still alive — %s (running %ds)", label, elapsed)


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


def check_e2e_coverage(changed_files: list[str]) -> tuple[bool, str]:
    """Verify the diff includes e2e test coverage for production changes.

    Returns (ok, message). On `ok=False` the caller should fail the
    review and surface `message` back to the dev agent.

    The gate fires when ALL of these are true:

    1. `changed_files` is non-empty.
    2. At least one changed file is NOT under a test-only prefix (i.e.
       the diff contains production-code changes).
    3. Zero changed files are under `web/e2e/`.

    This catches the ABS-202 failure mode where an agent writes a spec
    then deletes it, leaving the branch with a behavior change and zero
    e2e coverage — a violation of CLAUDE.md ("Every issue must be
    covered by Playwright e2e tests").
    """
    if not changed_files:
        return True, ""

    has_production_changes = any(
        not _is_test_only_path(p) for p in changed_files
    )
    if not has_production_changes:
        return True, ""

    has_e2e_changes = any(p.startswith("web/e2e/") for p in changed_files)
    if has_e2e_changes:
        return True, ""

    prod_files = [p for p in changed_files if not _is_test_only_path(p)]
    msg = (
        "Agent committed behavior change without e2e coverage. "
        "The diff contains production-code changes but no new or modified "
        "files under `web/e2e/`. Per CLAUDE.md, every issue must be covered "
        "by Playwright e2e tests.\n\n"
        "Production files changed:\n"
        + "\n".join(f"  - {p}" for p in prod_files)
    )
    return False, msg


def _scan_log_for_auth_error(log_file: str, start_offset: int) -> str | None:
    """Return a short excerpt around the first auth-marker hit in the log
    slice [`start_offset`:EOF], or None when no marker is present.

    The offset is captured by `IssueState.attempt_log_offset` before the
    current attempt starts to write to its append-only log file (see
    `_snapshot_log_offset` in agent.py). Scanning from that offset avoids
    false positives on auth strings left behind by an earlier attempt
    that shares the same log path. Mirrors the ABS-146 fix for
    `_is_rate_limited`.
    """
    if not log_file:
        return None
    try:
        with open(log_file, "rb") as fh:
            fh.seek(start_offset)
            raw = fh.read()
    except OSError:
        return None
    text = raw.decode("utf-8", errors="replace")
    lower = text.lower()
    for marker in AUTH_ERROR_MARKERS:
        idx = lower.find(marker)
        if idx == -1:
            continue
        # Small window around the hit so the operator sees the HTTP body /
        # status line that surrounded the marker, not just the marker
        # itself. Bounds are intentionally tight to keep Linear comments
        # readable.
        start = max(0, idx - 80)
        end = min(len(text), idx + len(marker) + 240)
        return text[start:end].strip()
    return None


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

    # ABS-202: e2e coverage gate — production changes must be accompanied
    # by at least one new/modified file under web/e2e/. Catches the
    # self-sabotage pattern where an agent writes a spec then deletes it.
    e2e_ok, e2e_msg = check_e2e_coverage(changed_files)
    if not e2e_ok:
        log.warning(
            "Review FAILED (e2e-coverage) for %s: %s",
            issue.identifier, e2e_msg.splitlines()[0],
        )
        return False, e2e_msg

    log.info(
        "reviewer: diff-vs-areas check (pass), e2e-coverage check (pass) "
        "→ LLM review starting for %s (model=%s)",
        issue.identifier, config.reviewer_model,
    )

    diff_proc = await asyncio.create_subprocess_exec(
        "git", "diff", "dev...HEAD",
        cwd=worktree,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    diff_out, _ = await diff_proc.communicate()
    diff_text = diff_out.decode("utf-8", errors="replace")

    if not diff_text.strip():
        # ABS-206: `review_and_gate`'s empty-diff guard should already
        # have blocked this issue before us. If a direct caller of
        # code_review reaches this branch, fail closed — auto-passing
        # an empty diff is what let ABS-203 land as Done after the
        # agent died on a 401.
        return False, (
            "Agent produced no commits — empty diff against dev. "
            "Re-run required."
        )

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
        "--output-format", "stream-json",
        "--model", config.reviewer_model,
        "--max-budget-usd", str(config.reviewer_token_limit),
        review_prompt,
    ]

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    jsonl_path = LOGS_DIR / f"{issue.identifier}-review.jsonl"

    t_start = time.monotonic()
    log.info(
        "Reviewer LLM started for %s (model=%s) — streaming to %s",
        issue.identifier, config.reviewer_model, jsonl_path,
    )

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=worktree,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    result_text = ""
    hb = asyncio.create_task(
        _periodic_heartbeat(f"LLM review for {issue.identifier} (model={config.reviewer_model})")
    )
    try:
        with jsonl_path.open("w", encoding="utf-8") as fh:
            assert proc.stdout is not None
            async for line_bytes in proc.stdout:
                decoded = line_bytes.decode("utf-8", errors="replace")
                fh.write(decoded)
                fh.flush()
                try:
                    event = json.loads(decoded)
                    if event.get("type") == "result":
                        result_text = event.get("result", "")
                except json.JSONDecodeError:
                    pass
        await proc.wait()
    finally:
        hb.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await hb

    elapsed = time.monotonic() - t_start
    raw = result_text.strip()
    log.info(
        "Reviewer LLM returned for %s (elapsed=%.0fs, bytes=%d)",
        issue.identifier, elapsed, len(raw),
    )

    try:
        result = json.loads(raw)
        # Defensive unwrap: if the text was double-encoded (legacy json format),
        # peel the outer envelope. stream-json delivers the text directly.
        if isinstance(result, dict) and "result" in result and set(result.keys()) <= {"result", "session_id", "type", "subtype", "cost_usd", "usage", "is_error"}:
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


def _extract_failing_tests_structured(worktree: str) -> list[_FailingTest]:
    """Parse Playwright results.json and return structured failing test data.

    Mirrors the logic in `_extract_failing_tests` but returns typed objects
    suitable for driving the solo re-run in `_classify_e2e_flake`.
    """
    results_path = Path(worktree) / "web" / "test-results" / "results.json"
    try:
        data = json.loads(results_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.debug("Could not read Playwright results.json from %s: %s", worktree, exc)
        return []

    failing: list[_FailingTest] = []

    def _walk_suites(suites: list, file_path: str, describe_stack: list[str]) -> None:
        for suite in suites:
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
                    failing.append(_FailingTest(spec_file=file_path, test_title=full_title))

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
                failing.append(_FailingTest(spec_file=file_path, test_title=spec_title))

    return failing


async def _classify_e2e_flake(
    worktree: str,
    issue_identifier: str,
    env: dict,
) -> tuple[bool, int]:
    """Classify whether failing e2e tests are flakes via solo re-run.

    Reads results.json from the *already-running* e2e stack (make e2e leaves
    it up when Playwright exits non-zero) and re-runs each failing test in
    isolation with `npx playwright test <spec> --grep <title>`.

    Returns (is_flake, n_recovered):
      is_flake=True  → all failures passed on solo re-run; treat as flake.
      n_recovered    → count of tests that recovered.

    Updates the in-memory _flake_history and logs flake_candidate=true for
    any test that has recovered ≥ _FLAKE_CANDIDATE_THRESHOLD times.
    """
    failing_tests = _extract_failing_tests_structured(worktree)
    if not failing_tests:
        log.debug(
            "classify_flake for %s: no structured failing tests found in results.json, "
            "treating as real failure",
            issue_identifier,
        )
        return False, 0

    log.info(
        "classify_flake for %s: %d failing test(s) found, attempting solo re-run",
        issue_identifier, len(failing_tests),
    )

    recovered = 0
    for test in failing_tests:
        grep_pattern = re.escape(test.test_title)
        cmd = [
            "npx", "playwright", "test",
            test.spec_file,
            "--grep", grep_pattern,
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(Path(worktree) / "web"),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
        stdout, _ = await proc.communicate()
        passed = proc.returncode == 0
        log.debug(
            "classify_flake solo re-run [%s > %s]: %s",
            test.spec_file, test.test_title, "PASS" if passed else "FAIL",
        )
        _flake_history[test.key].append(passed)
        if passed:
            recovered += 1
            flake_count = sum(1 for v in _flake_history[test.key] if v)
            if flake_count >= _FLAKE_CANDIDATE_THRESHOLD:
                log.warning(
                    "flake_candidate=true test='%s > %s' (recovered %d times across runs)",
                    test.spec_file, test.test_title, flake_count,
                )

    is_flake = recovered == len(failing_tests)
    return is_flake, recovered


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

    log.info(
        "E2E starting for %s (PG=%d API=%d WEB=%d)",
        issue.identifier, ports.pg, ports.api, ports.web,
    )
    proc = await asyncio.create_subprocess_exec(
        "make", "e2e",
        cwd=worktree,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=env,
    )
    hb = asyncio.create_task(_periodic_heartbeat(f"e2e run for {issue.identifier}"))
    try:
        stdout, _ = await proc.communicate()
    finally:
        hb.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await hb
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
        is_flake, n_recovered = await _classify_e2e_flake(worktree, issue.identifier, env)
        # Tear down the stack regardless; make e2e left it up on failure.
        _down = await asyncio.create_subprocess_exec(
            "./scripts/e2e-down.sh",
            cwd=worktree,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        await _down.communicate()
        if is_flake:
            log.info(
                "Flake recovered for %s: %d tests passed on solo re-run. flake_recovered=true",
                issue.identifier, n_recovered,
            )
            return True, tail + f"\n\nflake_recovered=true ({n_recovered} test(s) passed on solo re-run)"
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


async def _clean_agent_leaked_files(cwd: str) -> int:
    """Reset staged entries and remove untracked files leaked by agents.

    NM agents run in worktrees, but a misbehaving agent can write files via
    absolute paths into the main checkout (REPO_ROOT) and even ``git add``
    them there.  This poisons the index and causes every subsequent
    ``merge_to_dev`` to fail the clean-worktree precondition.

    This function runs ``git reset HEAD -- .`` (unstage everything) and
    ``git checkout -- .`` (discard working-tree modifications to tracked
    files), then ``git clean -fd`` (remove untracked files/dirs).  The
    combination is idempotent and safe when the operator has no legitimate
    WIP in the main checkout — which is always true during an NM run.

    Returns the number of dirty paths that were cleaned.
    """
    # Snapshot dirty paths *before* cleaning so we can log them.
    proc = await asyncio.create_subprocess_exec(
        "git", "status", "--porcelain",
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    porcelain = stdout.decode("utf-8", errors="replace").strip()
    if not porcelain:
        return 0

    dirty_lines = porcelain.splitlines()
    listing = "\n".join(f"  {line}" for line in dirty_lines)
    log.warning(
        "merge_to_dev: cleaning %d leaked file(s) from main checkout "
        "before merge:\n%s",
        len(dirty_lines),
        listing,
    )

    for cmd in (
        ["git", "reset", "HEAD", "--", "."],
        ["git", "checkout", "--", "."],
        ["git", "clean", "-fd"],
    ):
        p = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await p.communicate()

    log.info(
        "merge_to_dev: cleaned %d leaked files from main checkout before merge",
        len(dirty_lines),
    )
    return len(dirty_lines)


async def _rebase_branch_onto_dev(issue: IssueState) -> tuple[bool, str]:
    """Rebase the agent branch onto current dev tip before merge.

    Delegates to ``agent.rebase_worktree_onto_dev`` (ABS-160) via a
    lazy import to avoid a circular dependency (agent → reviewer for
    ``_combine_streams``).  Adds merge-context timing to the log line.
    """
    from .agent import rebase_worktree_onto_dev

    t0 = time.monotonic()
    ok, msg = await rebase_worktree_onto_dev(issue)
    elapsed = time.monotonic() - t0

    if ok:
        log.info(
            "merge_to_dev: rebased %s onto dev tip (%.1fs)",
            issue.identifier, elapsed,
        )
    return ok, msg


async def merge_to_dev(issue: IssueState) -> tuple[bool, str]:
    """
    Merge the issue's branch into dev. Returns (success, message).

    Merges are sequential — caller must ensure only one merge at a time.

    Before checking the clean-worktree precondition, rebases the agent
    branch onto the current dev tip (ABS-172). This absorbs sibling
    merges that landed on dev earlier in the same execution group.
    Without this, agents that touch files modified by an earlier sibling
    merge will hit a content conflict at the ``git merge`` step even
    though their worktree e2e passed.

    Then runs an idempotent cleanup pass to remove any files leaked
    into the main checkout by misbehaving agents (see ABS-171).

    After cleanup, the clean-worktree assertion still runs: if the
    checkout is *still* dirty (e.g. submodule state, lock files), the
    merge is refused with a clear error — same as before.
    """
    # ABS-172: rebase agent branch onto dev tip to absorb sibling merges.
    rebase_ok, rebase_err = await _rebase_branch_onto_dev(issue)
    if not rebase_ok:
        log.warning(
            "Rebase onto dev failed for %s: %s", issue.identifier, rebase_err,
        )
        return False, f"rebase-pre-merge failed: {rebase_err}"

    target = str(REPO_ROOT)
    await _clean_agent_leaked_files(target)

    clean, dirty_msg = await _assert_clean_worktree(target)
    if not clean:
        log.error("Merge precondition failed for %s: %s", issue.identifier, dirty_msg)
        return False, dirty_msg

    log.info("Merge starting for %s: %s", issue.identifier, issue.title)
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


async def revert_merge(issue: IssueState) -> tuple[bool, str, str]:
    """Revert the last merge on dev (used when post-merge regression detected).

    Returns (success, message, revert_sha). revert_sha is the short SHA of the
    revert commit on dev, or empty string on failure.
    """
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
        return False, f"Revert failed: {error}", ""

    sha_proc = await asyncio.create_subprocess_exec(
        "git", "rev-parse", "--short", "HEAD",
        cwd=str(REPO_ROOT),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    sha_out, _ = await sha_proc.communicate()
    revert_sha = sha_out.decode("utf-8", errors="replace").strip()

    push_proc = await asyncio.create_subprocess_exec(
        "git", "push", "origin", "dev",
        cwd=str(REPO_ROOT),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await push_proc.communicate()

    log.info("Reverted merge of %s on dev (sha=%s)", issue.identifier, revert_sha)
    return True, f"Reverted merge of {issue.identifier}", revert_sha


async def _diff_touches(worktree: str, path_prefix: str) -> bool:
    """Return True iff the branch diff (`dev...HEAD`) touches any file under `path_prefix`.

    Reuses `_git_changed_files` which already handles git errors gracefully.
    Must be called while the worktree is still on the feature branch (before
    `git checkout dev`) so that `dev...HEAD` resolves to the branch's commits.
    """
    changed = await _git_changed_files(worktree)
    norm = path_prefix.rstrip("/") + "/"
    return any(p.startswith(norm) or p == path_prefix.rstrip("/") for p in changed)


async def run_dev_e2e(issue: IssueState) -> tuple[bool, str]:
    """Run post-merge regression e2e from the agent's worktree.

    After merge_to_dev lands the branch on dev, we pull dev into the
    worktree so it has the merged state, then run `make e2e` there.
    This avoids the Next.js 16 same-directory conflict that kills the
    e2e server when a dev server is already running on the main checkout.

    If the merged diff touches any file under `scripts/night_manager/`,
    also runs `.venv/bin/pytest tests/night_manager/ -x --tb=short`.
    A pytest failure triggers the same revert path as a failing e2e.
    """
    worktree = issue.worktree
    ports = issue.ports
    assert worktree and ports

    # Capture whether this merge touched NM source BEFORE checking out dev,
    # because `git diff dev...HEAD` becomes empty once we switch to dev.
    touched_nm = await _diff_touches(worktree, "scripts/night_manager/")

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

    log.info(
        "Dev regression e2e starting for %s (PG=%d API=%d WEB=%d)",
        issue.identifier, ports.pg, ports.api, ports.web,
    )
    proc = await asyncio.create_subprocess_exec(
        "make", "e2e",
        cwd=worktree,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=env,
    )
    hb = asyncio.create_task(_periodic_heartbeat(f"dev regression e2e for {issue.identifier}"))
    try:
        stdout, _ = await proc.communicate()
    finally:
        hb.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await hb
    output = stdout.decode("utf-8", errors="replace")
    passed = proc.returncode == 0

    if passed:
        log.info("Dev regression e2e PASSED")
    else:
        log.info("Dev regression e2e FAILED")

    flake_msg = ""
    if not passed:
        tail = output[-5000:]
        failing_names = _extract_failing_tests(worktree)
        if failing_names:
            tail = failing_names + "\n\n" + tail
        # ABS-164: classify whether this is a flake before escalating.
        is_flake, n_recovered = await _classify_e2e_flake(worktree, issue.identifier, env)
        # Tear down the stack regardless; make e2e left it up on failure.
        _down = await asyncio.create_subprocess_exec(
            "./scripts/e2e-down.sh",
            cwd=worktree,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        await _down.communicate()
        if not is_flake:
            return False, tail
        log.info(
            "Flake recovered for %s: %d tests passed on solo re-run. flake_recovered=true",
            issue.identifier, n_recovered,
        )
        # Flake recovered counts as passed for the NM-pytest gate that
        # follows. Carry the flake annotation forward into the final
        # output so callers can still see what happened.
        passed = True
        flake_msg = f"\n\nflake_recovered=true ({n_recovered} test(s) passed on solo re-run)"

    # ABS-169: when the merged diff touched NM source, gate dev on the
    # NM unit-test suite — `make e2e` exercises the web app, not NM
    # itself, so a syntactically valid but semantically broken NM
    # change otherwise lands silently and breaks the next NM launch.
    if touched_nm:
        t_pytest = time.monotonic()
        log.info("NM source touched — running pytest tests/night_manager/ for %s", issue.identifier)
        pytest_proc = await asyncio.create_subprocess_exec(
            ".venv/bin/pytest", "tests/night_manager/", "-x", "--tb=short",
            cwd=str(REPO_ROOT),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        pytest_stdout, _ = await pytest_proc.communicate()
        pytest_elapsed = time.monotonic() - t_pytest
        pytest_passed = pytest_proc.returncode == 0
        pytest_output = pytest_stdout.decode("utf-8", errors="replace")
        status_word = "PASSED" if pytest_passed else "FAILED"
        log.info(
            "NM unit-test regression for %s: %s (%.0fs)",
            issue.identifier, status_word, pytest_elapsed,
        )
        if not pytest_passed:
            return False, f"NM unit-test regression failed; reverting\n\n{pytest_output[-3000:]}"

    return True, output[-5000:] + flake_msg


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

    # ABS-206: empty-diff guard. An agent that exits 0 without committing
    # anything (auth failure, sandbox crash, OOM) leaves the worktree on
    # dev tip. Code review then auto-passes the empty diff, e2e runs
    # against unmodified dev (green by definition), and `merge_to_dev`
    # merges nothing — silently flipping the ticket to Done. Catch the
    # non-delivery here, before any LLM call or e2e run, and surface the
    # auth root cause to the operator when the agent log shows one.
    changed_files = await _git_changed_files(issue.worktree)
    if not changed_files:
        auth_excerpt = _scan_log_for_auth_error(
            issue.log_file, issue.attempt_log_offset,
        )
        if auth_excerpt:
            reason = (
                "Agent produced no commits — authentication error detected "
                "in agent log. Re-run required after credentials are "
                f"refreshed.\n\nLog excerpt:\n{auth_excerpt}"
            )
        else:
            reason = (
                "Agent produced no commits — possible crash or auth failure. "
                "Re-run required."
            )
        log.warning(
            "Empty-diff guard fired for %s: %s",
            issue.identifier, reason.splitlines()[0],
        )
        issue.mark_blocked(reason)
        return False

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
