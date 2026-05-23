"""Plan parallel execution of issues, detecting conflicts via AI analysis."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import anthropic

from .config import NMConfig, PORT_BASE_PG, PORT_BASE_API, PORT_BASE_WEB, LOGS_DIR
from .linear_client import LinearIssue
from .state import ExecutionGroup, IssuePorts, IssueState, NMState

log = logging.getLogger("night_manager.planner")

CONFLICT_ANALYSIS_PROMPT = """\
You are a software engineering manager planning parallel development work.

Below are Linear issues to be developed concurrently by AI coding agents. Each \
agent works in its own git worktree (isolated copy of the repo). Agents CANNOT \
see each other's changes until their branches are merged.

Analyze these issues for potential conflicts — places where two agents working \
simultaneously would likely edit the same files or create merge conflicts.

Issues:
{issues_text}

Return a JSON object with this structure:
{{
  "groups": [
    {{
      "parallel": ["ABS-XX", "ABS-YY"],
      "rationale": "These issues touch independent areas"
    }},
    {{
      "parallel": ["ABS-ZZ"],
      "rationale": "This touches the same module as group 1, must run after"
    }}
  ],
  "conflict_notes": "Brief explanation of detected conflicts"
}}

Rules:
- Issues that touch different parts of the codebase can run in parallel
- Issues that likely touch the same files must be in separate sequential groups
- Frontend-only vs backend-only issues can usually run in parallel
- Group size must not exceed {max_agents} issues per group
- Order groups so that foundational/dependency issues run first
- If an issue involves deployment, put it in the last group
- Return ONLY the JSON, no markdown fences or commentary
"""


@dataclass
class ExecutionPlan:
    groups: list[ExecutionGroup]
    conflict_notes: str


async def plan_execution(
    issues: list[LinearIssue],
    config: NMConfig,
) -> ExecutionPlan:
    if len(issues) == 0:
        return ExecutionPlan(groups=[], conflict_notes="No issues to plan.")

    if len(issues) == 1:
        return ExecutionPlan(
            groups=[ExecutionGroup(parallel=[issues[0].identifier])],
            conflict_notes="Single issue — no conflict analysis needed.",
        )

    issues_text = "\n\n".join(
        f"**{issue.identifier}**: {issue.title}\n"
        f"Priority: {issue.priority}, Estimate: {issue.estimate or 'unset'}\n"
        f"Description: {(issue.description or 'No description')[:1000]}"
        for issue in issues
    )

    client = anthropic.AsyncAnthropic()
    try:
        response = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            messages=[{
                "role": "user",
                "content": CONFLICT_ANALYSIS_PROMPT.format(
                    issues_text=issues_text,
                    max_agents=config.max_agents,
                ),
            }],
        )
    finally:
        await client.close()

    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    data = json.loads(raw)
    groups = [
        ExecutionGroup(parallel=g["parallel"])
        for g in data["groups"]
    ]
    conflict_notes = data.get("conflict_notes", "")
    log.info("Planned %d groups: %s", len(groups), conflict_notes)
    return ExecutionPlan(groups=groups, conflict_notes=conflict_notes)


def allocate_ports(slot: int) -> IssuePorts:
    """Allocate a unique port triplet for the given slot (0-indexed)."""
    return IssuePorts(
        pg=PORT_BASE_PG + slot,
        api=PORT_BASE_API + slot,
        web=PORT_BASE_WEB + slot,
    )


def build_state_from_plan(
    plan: ExecutionPlan,
    issues: list[LinearIssue],
    config: NMConfig,
) -> NMState:
    """Create a NMState populated with issues and the execution plan."""
    state = NMState.new_run({
        "max_agents": config.max_agents,
        "label": config.label,
        "model": config.model,
        "deploy": config.deploy,
    })
    state.plan = plan.groups

    issue_map = {i.identifier: i for i in issues}
    slot = 0
    for group in plan.groups:
        for ident in group.parallel:
            issue = issue_map[ident]
            slug = _slugify(issue.title)
            branch = f"agent/{ident}-{slug}"
            log_file = str(LOGS_DIR / f"{ident}.jsonl")
            ports = allocate_ports(slot)
            slot += 1

            state.issues[ident] = IssueState(
                identifier=ident,
                title=issue.title,
                branch=branch,
                log_file=log_file,
                ports=ports,
                linear_id=issue.id,
            )

    return state


def _slugify(title: str) -> str:
    slug = title.lower()
    slug = "".join(c if c.isalnum() or c == " " else "" for c in slug)
    slug = "-".join(slug.split())
    return slug[:40]
