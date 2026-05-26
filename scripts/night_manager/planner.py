"""Plan parallel execution of issues, detecting conflicts via AI analysis."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any

from .config import NMConfig, PORT_BASE_PG, PORT_BASE_API, PORT_BASE_WEB, LOGS_DIR
from .linear_client import LinearIssue
from .state import ExecutionGroup, IssuePorts, IssueState, NMState

log = logging.getLogger("night_manager.planner")

CONFLICT_ANALYSIS_PROMPT = """\
You are a software engineering manager planning parallel development work.

Below are Linear issues to be developed concurrently by AI coding agents. Each \
agent works in its own git worktree (isolated copy of the repo). Agents CANNOT \
see each other's changes until their branches are merged.

Analyze these issues for:
1. Potential conflicts — places where two agents working simultaneously would \
likely edit the same files or create merge conflicts.
2. Complexity — choose the right model and effort level for each issue to \
balance quality against token cost.
3. Touch profile — a compact summary of where each issue is likely to write \
code, so a future replan can decide conflicts without re-reading descriptions.

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
  "issue_settings": {{
    "ABS-XX": {{
      "model": "sonnet",
      "effort": "medium",
      "rationale": "Config-only change, low complexity",
      "touch_profile": {{
        "areas": ["web/app/api/nm", "web/app/nm"],
        "kind": "frontend",
        "risk_tags": []
      }}
    }},
    "ABS-YY": {{
      "model": "opus",
      "effort": "high",
      "rationale": "New feature touching auth + billing, needs careful reasoning",
      "touch_profile": {{
        "areas": ["scripts/night_manager", "advisor/auth"],
        "kind": "backend",
        "risk_tags": ["auth", "billing"]
      }}
    }}
  }},
  "conflict_notes": "Brief explanation of detected conflicts"
}}

Grouping rules:
- Issues that touch different parts of the codebase can run in parallel
- Issues that likely touch the same files must be in separate sequential groups
- Frontend-only vs backend-only issues can usually run in parallel
- Group size must not exceed {max_agents} issues per group
- Order groups so that foundational/dependency issues run first
- If an issue involves deployment, put it in the last group

Touch profile rules:
- `areas`: 2–5 path-prefix tags identifying directories/modules the issue will \
likely touch (e.g. "scripts/night_manager", "web/app/api/nm", "advisor/db", \
"tests/advisor"). Be specific enough that two issues with disjoint area sets \
genuinely cannot collide.
- `kind`: one of "frontend", "backend", "infra", "docs", "tests", "fullstack".
- `risk_tags`: short tokens flagging sensitive surfaces ("auth", "billing", \
"migrations", "merge-logic", "ci", ...). Empty list if none apply.

Model/effort selection guide:
- Use "opus" + "high" for: new features, multi-file refactors, security-sensitive \
code (auth, billing, middleware), complex business logic, anything touching tests \
that need to pass e2e
- Use "sonnet" + "high" for: moderate features, adding UI components, writing \
tests for existing code, API endpoint additions
- Use "sonnet" + "medium" for: small changes, copy/config tweaks, adding a \
single file, documentation-like issues, straightforward bug fixes
- Use "haiku" + "medium" for: trivial changes, renaming, adding constants, \
updating labels/strings
- Default to config values: model="{default_model}", effort="{default_effort}" \
when unsure

Return ONLY the JSON, no markdown fences or commentary
"""


REPLAN_PROMPT = """\
You are a software engineering manager adding NEW Linear issues to a Night \
Manager run that is ALREADY IN FLIGHT. The existing issues are already \
assigned to groups; some are running right now and cannot be moved. You only \
decide where the NEW issues go.

Existing issues (touch profiles only — full descriptions are NOT shown, by \
design, to save tokens; you already classified these in the original plan):
{existing_text}

Pending groups currently in the plan (these are the ONLY groups you may \
extend; any group whose status is not "pending" is frozen):
{pending_groups_text}

New issues to slot in (full descriptions):
{new_issues_text}

Decide per new issue: either join an existing pending group (only if no \
conflict with any issue in that group AND the group has spare capacity, i.e. \
fewer than {max_agents} members), or open a NEW group. Two or more new issues \
that don't conflict with each other can share the same new_group key to run \
in parallel.

Return a JSON object:
{{
  "assignments": {{
    "ABS-130": {{ "join": 2, "rationale": "no overlap with ABS-101" }},
    "ABS-131": {{ "new_group": "g1", "rationale": "conflicts with both pending groups" }},
    "ABS-132": {{ "new_group": "g1", "rationale": "no overlap with ABS-131" }}
  }},
  "issue_settings": {{
    "ABS-130": {{
      "model": "sonnet",
      "effort": "medium",
      "rationale": "Config tweak",
      "touch_profile": {{
        "areas": ["web/app/api/nm"],
        "kind": "frontend",
        "risk_tags": []
      }}
    }}
  }},
  "conflict_notes": "Brief summary"
}}

Rules:
- `join` references a group number from the "Pending groups" list above \
(1-based, matching the list).
- `new_group` is an opaque string; new issues sharing a key are bundled into \
one new group (subject to {max_agents}).
- Model/effort selection guide is identical to the original plan; default to \
model="{default_model}" effort="{default_effort}" when unsure.
- Touch profile rules are identical to the original plan: 2–5 path-prefix \
`areas`, single `kind`, short `risk_tags`.

Return ONLY the JSON, no markdown fences or commentary
"""


@dataclass
class IssueSettings:
    model: str
    effort: str
    rationale: str = ""
    touch_profile: dict | None = None


@dataclass
class ExecutionPlan:
    groups: list[ExecutionGroup]
    conflict_notes: str
    issue_settings: dict[str, IssueSettings] | None = None


def _parse_touch_profile(raw: Any) -> dict | None:
    """Coerce the LLM's touch_profile output into a sane dict, or None."""
    if not isinstance(raw, dict):
        return None
    areas = raw.get("areas") or []
    kind = raw.get("kind") or "unknown"
    risk_tags = raw.get("risk_tags") or []
    if not isinstance(areas, list):
        areas = []
    if not isinstance(risk_tags, list):
        risk_tags = []
    return {
        "areas": [str(a) for a in areas][:8],
        "kind": str(kind),
        "risk_tags": [str(t) for t in risk_tags][:8],
    }


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

    prompt = CONFLICT_ANALYSIS_PROMPT.format(
        issues_text=issues_text,
        max_agents=config.max_agents,
        default_model=config.agent_model,
        default_effort=config.agent_effort,
    )

    proc = await asyncio.create_subprocess_exec(
        "claude", "-p",
        "--model", "sonnet",
        "--max-budget-usd", "1",
        prompt,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"Planner claude -p failed (exit={proc.returncode}): "
            f"{stderr.decode()[:500]}"
        )

    raw = stdout.decode("utf-8", errors="replace").strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    data = json.loads(raw)
    groups = [
        ExecutionGroup(parallel=g["parallel"])
        for g in data["groups"]
    ]
    conflict_notes = data.get("conflict_notes", "")

    issue_settings: dict[str, IssueSettings] = {}
    for ident, s in data.get("issue_settings", {}).items():
        issue_settings[ident] = IssueSettings(
            model=s.get("model", config.agent_model),
            effort=s.get("effort", config.agent_effort),
            rationale=s.get("rationale", ""),
            touch_profile=_parse_touch_profile(s.get("touch_profile")),
        )
    for issue in issues:
        if issue.identifier not in issue_settings:
            issue_settings[issue.identifier] = IssueSettings(
                model=config.agent_model,
                effort=config.agent_effort,
            )

    for ident, s in issue_settings.items():
        log.info(
            "  %s → model=%s effort=%s areas=%s (%s)",
            ident, s.model, s.effort,
            (s.touch_profile or {}).get("areas", []) if s.touch_profile else [],
            s.rationale or "default",
        )

    log.info("Planned %d groups: %s", len(groups), conflict_notes)
    return ExecutionPlan(groups=groups, conflict_notes=conflict_notes, issue_settings=issue_settings)


def _port_is_free(port: int) -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


_claimed_offsets: set[int] = set()


def allocate_ports(slot: int) -> IssuePorts:
    """Allocate a free port triplet, skipping occupied and already-claimed ports."""
    offset = slot
    for _ in range(50):
        if offset in _claimed_offsets:
            offset += 1
            continue
        pg = PORT_BASE_PG + offset
        api = PORT_BASE_API + offset
        web = PORT_BASE_WEB + offset
        if _port_is_free(pg) and _port_is_free(api) and _port_is_free(web):
            _claimed_offsets.add(offset)
            log.info("Allocated ports PG=%d API=%d WEB=%d (offset=%d)", pg, api, web, offset)
            return IssuePorts(pg=pg, api=api, web=web)
        log.debug("Port triplet offset=%d busy, trying next", offset)
        offset += 1
    raise RuntimeError(f"Could not find a free port triplet after 50 attempts (starting from slot {slot})")


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

            settings = plan.issue_settings.get(ident) if plan.issue_settings else None
            state.issues[ident] = IssueState(
                identifier=ident,
                title=issue.title,
                branch=branch,
                log_file=log_file,
                ports=ports,
                linear_id=issue.id,
                agent_model=settings.model if settings else config.agent_model,
                agent_effort=settings.effort if settings else config.agent_effort,
                touch_profile=settings.touch_profile if settings else None,
            )

    return state


def _slugify(title: str) -> str:
    slug = title.lower()
    slug = "".join(c if c.isalnum() or c == " " else "" for c in slug)
    slug = "-".join(slug.split())
    return slug[:40]


# ---------------------------------------------------------------------------
# In-flight replan
# ---------------------------------------------------------------------------


def _is_group_pending(state: NMState, group: ExecutionGroup) -> bool:
    """A group is pending iff every member is still queued (no agent started)."""
    if group.deploy:
        return False
    if not group.parallel:
        return False
    for ident in group.parallel:
        issue = state.issues.get(ident)
        if issue is None:
            return False
        if issue.status != "queued":
            return False
    return True


def _profile_str(profile: dict | None) -> str:
    """Compact one-line rendering of a touch profile for the replan prompt."""
    if not profile:
        return "areas=[unknown] kind=unknown"
    areas = profile.get("areas") or []
    kind = profile.get("kind") or "unknown"
    risk = profile.get("risk_tags") or []
    base = f"areas={areas} kind={kind}"
    if risk:
        base += f" risk={risk}"
    return base


def _build_replan_prompt(
    state: NMState,
    new_issues: list[LinearIssue],
    pending_indices: list[int],
    config: NMConfig,
) -> str:
    """Compose the compact replan prompt; existing issues contribute profiles
    only, never their full descriptions."""

    existing_lines: list[str] = []
    for grp_idx, group in enumerate(state.plan):
        if group.deploy:
            continue
        status_label = "PENDING" if _is_group_pending(state, group) else "FROZEN"
        for ident in group.parallel:
            issue = state.issues.get(ident)
            if not issue:
                continue
            existing_lines.append(
                f"- [{status_label}] {ident} group={grp_idx + 1} "
                f"status={issue.status} {_profile_str(issue.touch_profile)}"
            )
    existing_text = "\n".join(existing_lines) or "(no existing issues)"

    pending_groups_lines: list[str] = []
    for grp_idx in pending_indices:
        group = state.plan[grp_idx]
        spare = config.max_agents - len(group.parallel)
        pending_groups_lines.append(
            f"- group {grp_idx + 1}: {group.parallel} (spare capacity = {spare})"
        )
    pending_groups_text = "\n".join(pending_groups_lines) or "(no pending groups — every new issue must open a new group)"

    new_issues_text = "\n\n".join(
        f"**{issue.identifier}**: {issue.title}\n"
        f"Priority: {issue.priority}, Estimate: {issue.estimate or 'unset'}\n"
        f"Description: {(issue.description or 'No description')[:1000]}"
        for issue in new_issues
    )

    return REPLAN_PROMPT.format(
        existing_text=existing_text,
        pending_groups_text=pending_groups_text,
        new_issues_text=new_issues_text,
        max_agents=config.max_agents,
        default_model=config.agent_model,
        default_effort=config.agent_effort,
    )


def _apply_replan_assignments(
    state: NMState,
    new_issue_map: dict[str, LinearIssue],
    assignments: dict[str, dict],
    issue_settings: dict[str, IssueSettings],
    config: NMConfig,
    pending_indices: list[int],
) -> list[str]:
    """Mutate state.plan and state.issues per the LLM's assignments.

    Validates every "join" target is still pending and has capacity; falls
    back to a new appended group when it isn't. Bundles "new_group" peers
    sharing a key into a single group (split across multiple groups when
    they exceed max_agents).
    """
    pending_set = set(pending_indices)

    # Find a place to insert new groups: just before the trailing deploy group, if any.
    deploy_idx: int | None = None
    for idx, grp in enumerate(state.plan):
        if grp.deploy:
            deploy_idx = idx
            break

    def _new_group_insert_point() -> int:
        return deploy_idx if deploy_idx is not None else len(state.plan)

    new_group_clusters: dict[str, list[str]] = {}
    join_targets: dict[str, int] = {}

    for ident in new_issue_map:
        spec = assignments.get(ident) or {}
        join = spec.get("join")
        new_group_key = spec.get("new_group")

        target_group_idx: int | None = None
        if isinstance(join, int):
            candidate = join - 1  # prompt is 1-based
            if candidate in pending_set:
                group = state.plan[candidate]
                # Reserve capacity as we go, including pending joins from this batch.
                reserved = sum(1 for j in join_targets.values() if j == candidate)
                if len(group.parallel) + reserved < config.max_agents:
                    target_group_idx = candidate

        if target_group_idx is not None:
            join_targets[ident] = target_group_idx
        else:
            key = str(new_group_key) if new_group_key else "_default"
            new_group_clusters.setdefault(key, []).append(ident)

    # Apply join assignments
    added: list[str] = []
    for ident, grp_idx in join_targets.items():
        state.plan[grp_idx].parallel.append(ident)
        added.append(ident)
        log.info("Replan: %s joins pending group %d", ident, grp_idx + 1)

    # Apply new groups (split if cluster exceeds max_agents)
    for key, idents in new_group_clusters.items():
        for chunk_start in range(0, len(idents), config.max_agents):
            chunk = idents[chunk_start: chunk_start + config.max_agents]
            new_group = ExecutionGroup(parallel=list(chunk))
            insert_at = _new_group_insert_point()
            state.plan.insert(insert_at, new_group)
            if deploy_idx is not None:
                deploy_idx += 1
            added.extend(chunk)
            log.info(
                "Replan: new group at position %d for %s (cluster=%s)",
                insert_at + 1, chunk, key,
            )

    # Materialise IssueState entries for everything added
    start_slot = max(0, len(state.issues))
    slot = start_slot
    for ident in added:
        if ident in state.issues:
            continue
        issue = new_issue_map[ident]
        slug = _slugify(issue.title)
        settings = issue_settings.get(ident)
        ports = allocate_ports(slot)
        slot += 1
        state.issues[ident] = IssueState(
            identifier=ident,
            title=issue.title,
            branch=f"agent/{ident}-{slug}",
            log_file=str(LOGS_DIR / f"{ident}.jsonl"),
            ports=ports,
            linear_id=issue.id,
            agent_model=settings.model if settings else config.agent_model,
            agent_effort=settings.effort if settings else config.agent_effort,
            touch_profile=settings.touch_profile if settings else None,
            added_via_rescan=True,
        )

    return added


async def replan_with_new_issues(
    state: NMState,
    new_issues: list[LinearIssue],
    config: NMConfig,
) -> list[str]:
    """Add new issues to the in-flight plan with minimal token spend.

    Mutates state.plan and state.issues in place. Frozen (already-started)
    groups are never touched; new issues either join a pending group or
    form new groups appended before any deploy group.

    Returns the identifiers that were actually added to the plan.
    """
    # Filter to genuinely new identifiers
    new_issues = [i for i in new_issues if i.identifier not in state.issues]
    if not new_issues:
        return []

    new_issue_map = {i.identifier: i for i in new_issues}
    pending_indices = [
        idx for idx, grp in enumerate(state.plan) if _is_group_pending(state, grp)
    ]

    # Fast path: no pending groups to consider AND only one new issue -> just
    # append a new group, no LLM call.
    if not pending_indices and len(new_issues) == 1:
        only = new_issues[0]
        assignments = {only.identifier: {"new_group": "_solo"}}
        settings_map = {
            only.identifier: IssueSettings(
                model=config.agent_model,
                effort=config.agent_effort,
                rationale="Single rescan addition, no pending groups available",
            )
        }
        return _apply_replan_assignments(
            state, new_issue_map, assignments, settings_map, config, pending_indices,
        )

    prompt = _build_replan_prompt(state, new_issues, pending_indices, config)
    log.info(
        "Replan invoking LLM for %d new issue(s) against %d pending group(s)",
        len(new_issues), len(pending_indices),
    )

    proc = await asyncio.create_subprocess_exec(
        "claude", "-p",
        "--model", "sonnet",
        "--max-budget-usd", "0.5",
        prompt,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        log.warning(
            "Replan LLM call failed (exit=%d), falling back to new groups: %s",
            proc.returncode, stderr.decode()[:200],
        )
        assignments = {i.identifier: {"new_group": "_fallback"} for i in new_issues}
        settings_map = {
            i.identifier: IssueSettings(
                model=config.agent_model,
                effort=config.agent_effort,
                rationale="LLM replan failed, conservative fallback",
            )
            for i in new_issues
        }
        return _apply_replan_assignments(
            state, new_issue_map, assignments, settings_map, config, pending_indices,
        )

    raw = stdout.decode("utf-8", errors="replace").strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        log.warning("Replan JSON parse failed (%s), falling back to new groups", exc)
        assignments = {i.identifier: {"new_group": "_fallback"} for i in new_issues}
        data = {"assignments": assignments, "issue_settings": {}}

    assignments_raw = data.get("assignments") or {}
    assignments: dict[str, dict] = {}
    for ident in new_issue_map:
        spec = assignments_raw.get(ident)
        if isinstance(spec, dict):
            assignments[ident] = spec
        else:
            assignments[ident] = {"new_group": "_unassigned"}

    settings_map: dict[str, IssueSettings] = {}
    for ident, s in (data.get("issue_settings") or {}).items():
        if ident not in new_issue_map:
            continue
        settings_map[ident] = IssueSettings(
            model=s.get("model", config.agent_model),
            effort=s.get("effort", config.agent_effort),
            rationale=s.get("rationale", ""),
            touch_profile=_parse_touch_profile(s.get("touch_profile")),
        )
    for ident in new_issue_map:
        settings_map.setdefault(
            ident,
            IssueSettings(model=config.agent_model, effort=config.agent_effort),
        )

    conflict_notes = data.get("conflict_notes", "")
    if conflict_notes:
        log.info("Replan notes: %s", conflict_notes)

    return _apply_replan_assignments(
        state, new_issue_map, assignments, settings_map, config, pending_indices,
    )
