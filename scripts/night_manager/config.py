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

DEFAULT_MAX_AGENTS = 3
DEFAULT_LABEL = "Triaged"
DEFAULT_MODEL = "opus"
STUCK_TIMEOUT_MINUTES = 10
MAX_REVIEW_CYCLES = 3
MAX_E2E_FIX_CYCLES = 3

PORT_BASE_PG = 5433
PORT_BASE_API = 8002
PORT_BASE_WEB = 3002

WORKTREE_ROOT = REPO_ROOT / ".claude" / "worktrees"

ALLOWED_TOOLS = ",".join([
    "Read", "Write", "Edit", "Glob", "Grep", "Agent",
    "Bash(git *)", "Bash(make *)", "Bash(npm *)", "Bash(npx *)",
    "Bash(python *)", "Bash(.venv/bin/*)", "Bash(cd *)", "Bash(ls *)",
    "Bash(cat *)", "Bash(find *)", "Bash(grep *)", "Bash(mkdir *)",
    "Bash(cp *)", "Bash(mv *)", "Bash(head *)", "Bash(tail *)",
    "Bash(wc *)", "Bash(sort *)", "Bash(diff *)", "Bash(docker compose *)",
    "Bash(export *)", "Bash(echo *)", "Bash(touch *)", "Bash(chmod *)",
    "Bash(./scripts/*)", "Bash(source *)", "Bash(pip *)",
    "mcp__claude_ai_Linear__*",
])

DEV_AGENT_SYSTEM_PROMPT = """\
You are a development agent managed by the Night Manager.
Do not push to remote. Do not merge to dev. Do not deploy.
Commit frequently — small, logical units.
If you are stuck for more than 3 attempts on the same error, exit and \
describe the blocker in your final output.\
"""


@dataclass
class NMConfig:
    max_agents: int = DEFAULT_MAX_AGENTS
    label: str = DEFAULT_LABEL
    model: str = DEFAULT_MODEL
    dry_run: bool = False
    deploy: bool = False
    issue: str | None = None
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
                    help=f"Claude model for dev agents (default: {DEFAULT_MODEL})")
    p.add_argument("--dry-run", action="store_true",
                    help="Plan but don't execute — no agents spawned")
    p.add_argument("--deploy", action="store_true",
                    help="Enable full pipeline: promote dev→main, build, deploy")
    p.add_argument("--issue", type=str, default=None,
                    help="Run a single issue by ID (e.g. ABS-90)")
    args = p.parse_args(argv)
    return NMConfig(
        max_agents=args.max_agents,
        label=args.label,
        model=args.model,
        dry_run=args.dry_run,
        deploy=args.deploy,
        issue=args.issue,
    )
