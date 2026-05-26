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
MAX_REVIEW_CYCLES = 3
MAX_E2E_FIX_CYCLES = 3
MAX_REGRESSION_FIX_CYCLES = 3

PORT_BASE_PG = 5433
PORT_BASE_API = 8002
PORT_BASE_WEB = 3002

WORKTREE_ROOT = REPO_ROOT / ".claude" / "worktrees"

ALLOWED_TOOLS = ",".join([
    "Read", "Write", "Edit", "Glob", "Grep", "Agent",
    "Bash",
])

DEV_AGENT_SYSTEM_PROMPT = """\
You are a development agent managed by the Night Manager.

Rules:
- Do not push to remote. Do not merge to dev. Do not deploy.
- Do not update Linear issues — the Night Manager handles all Linear \
status updates, comments, and issue management on your behalf.
- Do not wait for user approval to merge — the Night Manager is your \
reviewer and will handle the merge after reviewing your work.
- Commit frequently — small, logical units.
- If you are stuck for more than 3 attempts on the same error, exit and \
describe the blocker in your final output.
- When you are done, exit with a summary of what you implemented and \
your test results. The Night Manager will take it from there.\
"""


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
    )
