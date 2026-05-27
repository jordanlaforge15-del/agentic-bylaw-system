"""Configuration constants and CLI argument parsing."""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
NM_DIR = REPO_ROOT / ".night-manager"
LOGS_DIR = NM_DIR / "logs"
STATE_FILE = NM_DIR / "state.json"
# Sentinel the user can touch to force an in-flight rescan at the next group
# boundary. The manager already polls Linear at every boundary; this file is
# an "I added work, please notice" signal that just gets logged and consumed.
NUDGE_FILE = NM_DIR / "nudge"

DEFAULT_MAX_AGENTS = 2
DEFAULT_LABEL = "Triaged"
DEFAULT_MODEL = "opus"
DEFAULT_AGENT_MODEL = "opus"
DEFAULT_AGENT_EFFORT = "high"
DEFAULT_AGENT_TOKEN_LIMIT = 10.0  # --max-budget-usd value; token guardrail, not real cost
DEFAULT_REVIEWER_MODEL = "sonnet"
DEFAULT_REVIEWER_TOKEN_LIMIT = 2.0
# Default watchdog stall threshold. `make e2e` is documented at ~9 min on a
# cold stack and is routinely the last action of a resume cycle (Bash tool
# blocks stdout for the entire run — see RCA for ABS-121). 10 min was too
# tight; 20 min sits comfortably above the e2e ceiling without letting truly
# wedged agents idle forever. Override per-deploy via NM_AGENT_STALL_MIN.
STUCK_TIMEOUT_MINUTES = int(os.environ.get("NM_AGENT_STALL_MIN", "20"))
# When the last stream-json event is an unterminated Bash tool_use, the
# watchdog extends grace to this many minutes for that tool call. Covers
# `make e2e` on a cold stack with headroom for slow CI machines. Override
# via NM_BASH_TOOL_BUDGET_MIN.
BASH_TOOL_BUDGET_MINUTES = int(os.environ.get("NM_BASH_TOOL_BUDGET_MIN", "30"))
# Post-success idle threshold (see ABS-159). When the most recent stream-json
# event is a `result` block with `subtype: success` / `is_error: false`, the
# agent's real work is finished — anything after that is the wrapper draining.
# If the wrapper is still alive this many minutes later, an orphaned child
# (e.g. `npx playwright show-report` serving on :9323) is holding it open;
# SIGTERM the wrapper so NM can enter the merge gate. Override via
# NM_POST_SUCCESS_IDLE_MIN.
POST_SUCCESS_IDLE_MINUTES = int(os.environ.get("NM_POST_SUCCESS_IDLE_MIN", "5"))
MAX_REVIEW_CYCLES = 3
MAX_E2E_FIX_CYCLES = 3
MAX_REGRESSION_FIX_CYCLES = 3

# Wall-clock timeout caps (B.1). These are hard limits independent of the
# idle watchdog — they bound the maximum calendar time a single agent can
# consume regardless of activity. Override via env vars if needed.
WALL_CLOCK_MAX_MINUTES_INITIAL = int(
    os.environ.get("NM_WALL_CLOCK_MAX_MIN_INITIAL", "60")
)
WALL_CLOCK_MAX_MINUTES_CONTINUATION = int(
    os.environ.get("NM_WALL_CLOCK_MAX_MIN_CONTINUATION", "30")
)

PORT_BASE_PG = 5433
PORT_BASE_API = 8002
PORT_BASE_WEB = 3002

WORKTREE_ROOT = REPO_ROOT / ".claude" / "worktrees"

# MCP server prefix for the Linear integration in this environment.
# The two read-only tools allow agents to re-fetch ticket text and
# reviewer comments mid-task; write tools are explicitly excluded.
LINEAR_MCP_PREFIX = "mcp__ba80716d-b9e6-45ec-b2a1-85da85de3d2a__"

ALLOWED_TOOLS = ",".join([
    "Read", "Write", "Edit", "Glob", "Grep", "Agent",
    "Bash",
    f"{LINEAR_MCP_PREFIX}get_issue",
    f"{LINEAR_MCP_PREFIX}list_comments",
])

DEV_AGENT_SYSTEM_PROMPT = (
    "You are a development agent managed by the Night Manager.\n"
    "\n"
    "Rules:\n"
    "- Do not push to remote. Do not merge to dev. Do not deploy.\n"
    "- WORKTREE ISOLATION: All file operations (Read, Write, Edit, Bash) "
    "must stay within your assigned worktree directory. Never write to, "
    "`cd` into, or `git add` in the main project checkout — only your "
    "worktree copy. The main checkout path will look like the project root "
    "without `.claude/worktrees/<name>/` in it. If you use an absolute path, "
    "it MUST start with your worktree path. Writing to the main checkout "
    "corrupts the git index for every other agent and blocks all subsequent "
    "merges.\n"
    "  Example — WRONG: Write to `/Users/chris/project/docs/foo.md`\n"
    "  Example — RIGHT: Write to `/Users/chris/project/.claude/worktrees/"
    "nm-abs-42/docs/foo.md`\n"
    f"- You may READ Linear via `{LINEAR_MCP_PREFIX}get_issue` and "
    f"`{LINEAR_MCP_PREFIX}list_comments` — use this any time you need to "
    "re-check the ticket text, acceptance criteria, or prior reviewer notes mid-task.\n"
    "- You may NOT write to Linear. Never call `save_issue`, `save_comment`, "
    "`save_document`, or any other Linear write/delete tool — the Night "
    "Manager handles all Linear updates on your behalf.\n"
    "- Do not wait for user approval to merge — the Night Manager is your "
    "reviewer and will handle the merge after reviewing your work.\n"
    "- Commit frequently — small, logical units.\n"
    "- If you are stuck for more than 3 attempts on the same error, exit and "
    "describe the blocker in your final output.\n"
    "- Never run commands that start a long-running server in the foreground "
    "(examples: `npx playwright show-report`, `playwright codegen`, "
    "`python -m http.server`, `npm run dev`, `vite preview`, `make e2e-up` "
    "without `e2e-down`). If you need to inspect Playwright results, read "
    "`web/test-results/.last-run.json` or `web/playwright-report/index.html` "
    "— do not serve them. If a command must run in the background, prefix it "
    "with `timeout 30s` and explain in your output that you did so.\n"
    "- When you are done, exit with a summary of what you implemented and "
    "your test results. The Night Manager will take it from there."
)


@dataclass
class NMConfig:
    max_agents: int = DEFAULT_MAX_AGENTS
    label: str = DEFAULT_LABEL
    model: str = DEFAULT_MODEL
    agent_model: str = DEFAULT_AGENT_MODEL
    agent_effort: str = DEFAULT_AGENT_EFFORT
    agent_token_limit: float = DEFAULT_AGENT_TOKEN_LIMIT
    reviewer_model: str = DEFAULT_REVIEWER_MODEL
    reviewer_token_limit: float = DEFAULT_REVIEWER_TOKEN_LIMIT
    dry_run: bool = False
    deploy: bool = False
    issue: str | None = None
    resume: bool = False
    resume_issue: str | None = None
    resume_queued: bool = False
    repo_root: Path = field(default_factory=lambda: REPO_ROOT)
    linear_api_key: str = field(default_factory=lambda: os.environ.get("LINEAR_API_KEY", ""))

    def __post_init__(self) -> None:
        if not self.linear_api_key:
            from dotenv import load_dotenv
            load_dotenv(self.repo_root / ".env")
            # Worktrees share the main repo's .env via git commondir
            git_common = self.repo_root / ".git"
            if git_common.is_file():
                commondir = git_common.read_text().strip().split("gitdir: ", 1)[-1]
                main_root = Path(commondir).resolve().parents[2]
                load_dotenv(main_root / ".env")
            self.linear_api_key = os.environ.get("LINEAR_API_KEY", "")
        if not self.linear_api_key:
            raise RuntimeError("LINEAR_API_KEY not found in environment or .env")


def parse_args(argv: list[str] | None = None) -> NMConfig:
    p = argparse.ArgumentParser(
        prog="night-manager",
        description="Overnight orchestrator for Linear issue development",
    )
    p.add_argument("--max-agents", type=int, default=DEFAULT_MAX_AGENTS,
                    help=f"Max parallel dev agents (default: {DEFAULT_MAX_AGENTS})")
    p.add_argument("--label", default=DEFAULT_LABEL,
                    help=f"Linear label to filter issues (default: {DEFAULT_LABEL})")
    p.add_argument("--model", default=DEFAULT_MODEL,
                    help=f"Claude model for planner (default: {DEFAULT_MODEL})")
    p.add_argument("--agent-model", default=DEFAULT_AGENT_MODEL,
                    help=f"Claude model for dev agents (default: {DEFAULT_AGENT_MODEL})")
    p.add_argument("--agent-effort", default=DEFAULT_AGENT_EFFORT,
                    choices=["low", "medium", "high"],
                    help=f"Effort level for dev agents (default: {DEFAULT_AGENT_EFFORT})")
    p.add_argument("--agent-token-limit", type=float, default=DEFAULT_AGENT_TOKEN_LIMIT,
                    help=f"Estimated-USD cap per agent session — Claude Code's --max-budget-usd (default: {DEFAULT_AGENT_TOKEN_LIMIT})")
    p.add_argument("--reviewer-model", default=DEFAULT_REVIEWER_MODEL,
                    help=f"Claude model for code review (default: {DEFAULT_REVIEWER_MODEL})")
    p.add_argument("--reviewer-token-limit", type=float, default=DEFAULT_REVIEWER_TOKEN_LIMIT,
                    help=f"Estimated-USD cap per review — Claude Code's --max-budget-usd (default: {DEFAULT_REVIEWER_TOKEN_LIMIT})")
    p.add_argument("--dry-run", action="store_true",
                    help="Plan but don't execute — no agents spawned")
    p.add_argument("--deploy", action="store_true",
                    help="Enable full pipeline: promote dev→main, build, deploy")
    p.add_argument("--issue", type=str, default=None,
                    help="Run a single issue by ID (e.g. ABS-90)")
    p.add_argument("--resume", action="store_true",
                    help="Resume the last run — re-execute failed/rate-limited issues")
    p.add_argument("--resume-issue", type=str, default=None,
                    help="Resume a single issue from the last run (e.g. ABS-90)")
    p.add_argument("--resume-queued", action="store_true",
                    help=(
                        "Also resume issues that never started (status=queued). "
                        "Off by default because never-started issues may have "
                        "been merged out-of-band; the sanity-check pass will "
                        "reconcile against dev before re-spawning."
                    ))
    args = p.parse_args(argv)
    return NMConfig(
        max_agents=args.max_agents,
        label=args.label,
        model=args.model,
        agent_model=args.agent_model,
        agent_effort=args.agent_effort,
        agent_token_limit=args.agent_token_limit,
        reviewer_model=args.reviewer_model,
        reviewer_token_limit=args.reviewer_token_limit,
        dry_run=args.dry_run,
        deploy=args.deploy,
        issue=args.issue,
        resume=args.resume,
        resume_issue=args.resume_issue,
        resume_queued=args.resume_queued,
    )
