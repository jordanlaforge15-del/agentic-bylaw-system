#!/usr/bin/env python3
"""Investigate coverage gaps in an ingested bylaw document.

Pipeline (cheap-tool-first triage, not auto-fix):

    1. Run `layer1 verify-coverage <doc_id>` -> JSON coverage report.
    2. Filter gaps by severity (default: high + medium).
    3. Cluster gaps by (gap_type, page bucket) so the LLM sees patterns,
       not individual occurrences.
    4. For each cluster, run `layer1 audit-page` on a representative page
       to gather source-vs-persisted context.
    5. Send cluster + audit context to `claude -p` with a JSON schema and
       collect a root-cause hypothesis + recommended action.
    6. Emit a markdown report grouping clusters by recommended action.

The LLM's job is *triage*, not *fix*. Output flags clusters as one of:
    - file-issue     : worth a Linear issue, root cause is plausibly a bug
    - manual-review  : ambiguous, needs human eyes on the page
    - accept-as-noise: expected artifact (source-typos, dead refs, etc.)

Run from the project root with the venv active:

    layer1 verify-coverage 5 --out /tmp/cov-5.json  # optional, the script
                                                    # runs this for you
    .venv/bin/python scripts/investigate_coverage.py 5 --out report.md

Useful flags:
    --severity high,medium      gap severities to investigate (default)
    --max-clusters 20           cap how many clusters to investigate
    --deep                      investigate every gap individually instead
                                of clustering (~5-10x more Claude calls)
    --skip-llm                  produce report without LLM analysis (cheap
                                dry-run; useful for inspecting clusters)
    --claude-bin <path>         override the `claude` CLI binary path
    --db-url <url>              forwarded to layer1 commands
    --promote-to-linear         open Linear issues for file-issue clusters
                                with medium/high confidence (needs
                                LINEAR_API_KEY env var)
    --linear-dry-run            preview what would be filed without
                                calling the Linear API
    --linear-team ABS           which Linear team to file under
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence


# --------------------------------------------------------------------------- #
# Data shapes                                                                 #
# --------------------------------------------------------------------------- #


@dataclass
class GapCluster:
    """A group of gaps that share a gap_type and a page bucket.

    A cluster is the unit the LLM investigates. One LLM call per cluster.
    """

    gap_type: str
    severity: str
    pages: list[int] = field(default_factory=list)
    samples: list[str] = field(default_factory=list)
    details: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return max(len(self.pages), len(self.details))

    @property
    def representative_page(self) -> int | None:
        return self.pages[0] if self.pages else None


@dataclass
class Investigation:
    """Result of one Claude call against a cluster."""

    cluster: GapCluster
    hypothesis: str = ""
    evidence: str = ""
    suspected_files: list[str] = field(default_factory=list)
    recommended_action: str = "manual-review"
    confidence: str = "low"
    rationale: str = ""
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.error is None and bool(self.hypothesis)


# --------------------------------------------------------------------------- #
# External-command runners                                                    #
# --------------------------------------------------------------------------- #


def _layer1_cmd() -> list[str]:
    """Build the prefix for invoking the layer1 CLI.

    The Typer app in layer1/cli.py has no `__main__` guard, so
    `python -m layer1.cli` is a no-op. We resolve the installed
    console script next to the current Python interpreter -- this
    makes the script work whether or not the caller activated the
    venv, as long as they run it via the venv's python.
    """
    candidate = Path(sys.executable).parent / "layer1"
    if candidate.exists():
        return [str(candidate)]
    on_path = shutil.which("layer1")
    if on_path:
        return [on_path]
    raise SystemExit(
        "Could not locate the `layer1` console script. Run this with "
        "`.venv/bin/python scripts/investigate_coverage.py ...` or activate the venv."
    )


def _run_layer1_to_json(cmd_args: list[str], *, label: str) -> dict[str, Any]:
    """Run a layer1 sub-command with `--out <tempfile>` and load the JSON.

    Going through --out keeps the CLI's colored stdout banners from
    contaminating the JSON we have to parse.
    """
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as tf:
        tmp_path = Path(tf.name)
    try:
        cmd = _layer1_cmd() + cmd_args + ["--out", str(tmp_path)]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode not in (0, 1):
            # verify-coverage exits 1 on grade D/F -- that's fine, we still
            # have the report. Anything else is a real failure.
            raise RuntimeError(
                f"{label} failed (exit {result.returncode}): "
                f"{result.stderr.strip()[:500] or result.stdout.strip()[:500]}"
            )
        text = tmp_path.read_text(encoding="utf-8")
        if not text.strip():
            raise RuntimeError(
                f"{label} wrote no JSON (stdout: {result.stdout[:200]!r})"
            )
        return json.loads(text)
    finally:
        tmp_path.unlink(missing_ok=True)


def run_verify_coverage(
    document_id: int,
    *,
    db_url: str | None = None,
) -> dict[str, Any]:
    """Invoke `layer1 verify-coverage <doc_id>` and return its JSON payload."""
    args = ["verify-coverage", str(document_id)]
    if db_url:
        args += ["--db-url", db_url]
    return _run_layer1_to_json(args, label="verify-coverage")


def run_audit_page(
    document_id: int,
    page: int,
    *,
    db_url: str | None = None,
) -> dict[str, Any]:
    """Invoke `layer1 audit-page <doc_id> <page>` and return its JSON payload."""
    args = ["audit-page", str(document_id), str(page)]
    if db_url:
        args += ["--db-url", db_url]
    return _run_layer1_to_json(args, label=f"audit-page {document_id}/{page}")


# --------------------------------------------------------------------------- #
# Clustering                                                                  #
# --------------------------------------------------------------------------- #


_SEVERITY_RANK = {"high": 3, "medium": 2, "low": 1}


def filter_gaps(
    gaps: list[dict[str, Any]],
    *,
    severities: set[str],
) -> list[dict[str, Any]]:
    return [g for g in gaps if g.get("severity") in severities]


def cluster_gaps(gaps: list[dict[str, Any]], *, deep: bool) -> list[GapCluster]:
    """Group gaps for LLM investigation.

    In normal mode, one cluster per gap_type -- the page list captures the
    spread. In `--deep` mode, every gap becomes its own cluster so the
    LLM gets to look at each page individually.
    """
    if deep:
        clusters: list[GapCluster] = []
        for gap in gaps:
            clusters.append(
                GapCluster(
                    gap_type=gap.get("gap_type", "unknown"),
                    severity=gap.get("severity", "unknown"),
                    pages=[gap["page"]] if gap.get("page") else [],
                    samples=[gap["sample_text"]] if gap.get("sample_text") else [],
                    details=[gap.get("detail", "")],
                )
            )
        return clusters

    buckets: dict[str, GapCluster] = {}
    for gap in gaps:
        key = gap.get("gap_type", "unknown")
        cluster = buckets.get(key)
        if cluster is None:
            cluster = GapCluster(gap_type=key, severity=gap.get("severity", "unknown"))
            buckets[key] = cluster
        # Bubble up to the most severe seen for this type.
        if _SEVERITY_RANK.get(gap.get("severity", ""), 0) > _SEVERITY_RANK.get(
            cluster.severity, 0
        ):
            cluster.severity = gap["severity"]
        if gap.get("page") and gap["page"] not in cluster.pages:
            cluster.pages.append(gap["page"])
        if gap.get("sample_text") and len(cluster.samples) < 3:
            cluster.samples.append(gap["sample_text"])
        if gap.get("detail") and len(cluster.details) < 5:
            cluster.details.append(gap["detail"])

    for cluster in buckets.values():
        cluster.pages.sort()
    return sorted(
        buckets.values(),
        key=lambda c: (-_SEVERITY_RANK.get(c.severity, 0), -c.count, c.gap_type),
    )


# --------------------------------------------------------------------------- #
# Claude invocation                                                           #
# --------------------------------------------------------------------------- #


_SYSTEM_PROMPT = """You are a senior parser/ingest engineer for a bylaw normalization pipeline.

The pipeline lives in src/layer1/ -- parsers extract text from PDFs into
PageBlocks, then a fragmenter assembles SourceFragments with a citation
tree, plus SourceTables and CrossReferences. `verify_coverage.py`
compares the original PDF text against what was persisted and flags
gaps.

Your job here is TRIAGE, not fix. For each gap cluster, decide whether
it's a real bug worth filing, ambiguous enough to need human eyes, or
expected noise (e.g. typos in the source PDF, dead cross-references the
legislators themselves left).

Be specific. "Check the parser" is not useful. "src/layer1/parsers/X.py
probably miscategorizes block_type=Y when..." is useful. Use the
codebase tools available to ground your suspect-file pointers when the
gap type warrants it. Be honest about confidence -- low confidence is
fine; bluffing is not.
"""


def _build_user_prompt(
    cluster: GapCluster,
    audit: dict[str, Any] | None,
    coverage_summary: dict[str, Any],
    doc_meta: dict[str, Any],
) -> str:
    parts: list[str] = []
    parts.append(f"# Document\n")
    parts.append(
        f"- ID: {doc_meta.get('document_id')}\n"
        f"- Municipality: {doc_meta.get('municipality')}\n"
        f"- Bylaw: {doc_meta.get('bylaw_name')}\n"
        f"- Page count: {coverage_summary.get('page_count')}\n"
        f"- Overall grade: {coverage_summary.get('grade')} "
        f"(mean overlap {coverage_summary.get('mean_page_overlap')})\n"
    )

    parts.append("\n# Gap cluster\n")
    parts.append(
        f"- Type: `{cluster.gap_type}`\n"
        f"- Severity: {cluster.severity}\n"
        f"- Occurrences: {cluster.count}\n"
        f"- Pages affected: {cluster.pages or 'document-wide'}\n"
    )
    if cluster.details:
        parts.append("\n## Sample detail messages\n")
        for d in cluster.details:
            parts.append(f"- {d}\n")
    if cluster.samples:
        parts.append("\n## Sample text from affected pages\n")
        for s in cluster.samples:
            parts.append(f"> {s[:400]}\n\n")

    if audit:
        page_results = audit.get("page_results", [])
        if page_results:
            pr = page_results[0]
            parts.append(
                f"\n# Audit snapshot for representative page {pr.get('page_number')}\n"
            )
            parts.append(f"- Risk score: {pr.get('risk_score')}\n")
            reasons = pr.get("risk_reasons") or []
            if reasons:
                parts.append(f"- Risk reasons: {', '.join(reasons)}\n")
            checks = pr.get("deterministic_checks") or {}
            if checks:
                parts.append("\n## Deterministic checks\n")
                parts.append("```json\n" + json.dumps(checks, indent=2)[:2000] + "\n```\n")

    parts.append(
        "\n# Task\n"
        "Respond with EXACTLY ONE JSON object and NOTHING ELSE. No markdown,\n"
        "no code fences, no explanatory prose before or after. The object\n"
        "must have these keys:\n\n"
        "  - hypothesis (string, 1-2 sentences)\n"
        "  - evidence (string, what supports the hypothesis)\n"
        "  - suspected_files (array of strings; real paths under src/layer1/\n"
        "    where possible, [] if unknown)\n"
        "  - recommended_action (one of: \"file-issue\", \"manual-review\",\n"
        "    \"accept-as-noise\")\n"
        "  - confidence (one of: \"low\", \"medium\", \"high\")\n"
        "  - rationale (string, why this action and confidence)\n\n"
        "If the gap pattern is a known limitation of normalizing legal\n"
        "prose (typos, dead cross-references the legislators left, citation\n"
        "ambiguity on bullet lists), recommend `accept-as-noise` and say so\n"
        "in `rationale`. Your entire response must be parseable by\n"
        "`json.loads()` -- start with `{` and end with `}`.\n"
    )
    return "".join(parts)


_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json_object(text: str) -> dict[str, Any] | None:
    """Pull the first balanced JSON object out of a possibly-noisy string.

    Claude sometimes wraps JSON in ```json fences or precedes it with a
    short preamble despite our instructions. This is a defensive parser
    that handles those cases.
    """
    stripped = text.strip()
    if stripped.startswith("```"):
        # Strip a leading ```json (or ```) fence and any trailing fence.
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```\s*$", "", stripped)
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    match = _JSON_OBJECT_RE.search(stripped)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def investigate_with_claude(
    cluster: GapCluster,
    audit: dict[str, Any] | None,
    coverage_summary: dict[str, Any],
    doc_meta: dict[str, Any],
    *,
    claude_bin: str,
    project_dir: Path,
    timeout_s: int = 180,
    max_budget_usd: float = 0.5,
) -> Investigation:
    inv = Investigation(cluster=cluster)
    user_prompt = _build_user_prompt(cluster, audit, coverage_summary, doc_meta)

    cmd = [
        claude_bin,
        "-p",
        user_prompt,
        "--append-system-prompt",
        _SYSTEM_PROMPT,
        "--add-dir",
        str(project_dir),
        "--output-format",
        "json",
        "--max-budget-usd",
        str(max_budget_usd),
        "--allowedTools",
        "Read Grep Glob",
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            cwd=str(project_dir),
        )
    except subprocess.TimeoutExpired:
        inv.error = f"claude call timed out after {timeout_s}s"
        return inv

    if result.returncode != 0:
        inv.error = (
            f"claude exited {result.returncode}: "
            f"{result.stderr.strip()[:500] or result.stdout.strip()[:500]}"
        )
        return inv

    # `--output-format json` wraps the model output in a transcript envelope.
    # We pull out the assistant's structured result. The envelope shape is:
    #   {"type": "result", "result": "<json string>", ...}
    raw = result.stdout.strip()
    try:
        envelope = json.loads(raw)
    except json.JSONDecodeError:
        inv.error = f"could not parse claude envelope (first 300 chars): {raw[:300]}"
        return inv

    payload_str = envelope.get("result") if isinstance(envelope, dict) else None
    if isinstance(payload_str, str):
        payload = _extract_json_object(payload_str)
        if payload is None:
            inv.error = f"could not extract JSON from claude payload: {payload_str[:300]}"
            return inv
    elif isinstance(envelope, dict) and "hypothesis" in envelope:
        # Some versions emit the structured output directly without envelope.
        payload = envelope
    else:
        inv.error = f"unexpected claude envelope shape: {raw[:300]}"
        return inv

    inv.hypothesis = str(payload.get("hypothesis", "")).strip()
    inv.evidence = str(payload.get("evidence", "")).strip()
    inv.suspected_files = [str(p) for p in payload.get("suspected_files", []) if p]
    inv.recommended_action = str(
        payload.get("recommended_action", "manual-review")
    ).strip()
    inv.confidence = str(payload.get("confidence", "low")).strip()
    inv.rationale = str(payload.get("rationale", "")).strip()
    return inv


# --------------------------------------------------------------------------- #
# Markdown rendering                                                          #
# --------------------------------------------------------------------------- #


_ACTION_ORDER = ["file-issue", "manual-review", "accept-as-noise"]
_ACTION_LABELS = {
    "file-issue": "Worth filing",
    "manual-review": "Needs human review",
    "accept-as-noise": "Acceptable noise",
}

# Confidence levels at which the script will auto-promote a file-issue
# cluster to a Linear issue. Low-confidence findings need human review
# first -- they're flagged in the report but not filed.
_PROMOTABLE_CONFIDENCE = {"medium", "high"}


def build_linear_issue_body(
    inv: "Investigation",
    *,
    doc_meta: dict[str, Any],
    coverage_summary: dict[str, Any],
) -> str:
    """Render the markdown body that gets posted to Linear for a cluster."""
    c = inv.cluster
    lines: list[str] = []
    lines.append(
        f"Coverage investigator flagged a `{c.gap_type}` gap during ingest "
        f"verification of **{doc_meta.get('bylaw_name')}** "
        f"(doc {doc_meta.get('document_id')}).\n"
    )
    lines.append(
        f"\n- **Severity:** {c.severity}\n"
        f"- **Occurrences:** {c.count}\n"
        f"- **Document grade:** {coverage_summary.get('grade')} "
        f"(mean overlap {coverage_summary.get('mean_page_overlap')})\n"
        f"- **LLM confidence:** {inv.confidence}\n"
    )
    if c.pages:
        pages_str = ", ".join(str(p) for p in c.pages[:20])
        if len(c.pages) > 20:
            pages_str += f", ... (+{len(c.pages) - 20} more)"
        lines.append(f"- **Pages affected:** {pages_str}\n")
    lines.append(f"\n## Hypothesis\n\n{inv.hypothesis}\n")
    if inv.evidence:
        lines.append(f"\n## Evidence\n\n{inv.evidence}\n")
    if inv.suspected_files:
        lines.append("\n## Suspected files\n\n")
        for f in inv.suspected_files:
            lines.append(f"- `{f}`\n")
    if inv.rationale:
        lines.append(f"\n## Why this was auto-filed\n\n{inv.rationale}\n")
    if c.details:
        lines.append("\n## Raw gap details\n\n")
        for d in c.details:
            lines.append(f"- {d}\n")
    lines.append(
        "\n---\n"
        "_Filed automatically by `scripts/investigate_coverage.py`. "
        "Re-run with `--skip-llm --coverage-json <path>` to regenerate "
        "the analysis without re-querying the LLM._\n"
    )
    return "".join(lines)


def build_linear_issue_title(
    inv: "Investigation",
    doc_meta: dict[str, Any],
) -> str:
    """Stable title format so duplicates are spottable in Linear's list view."""
    doc_id = doc_meta.get("document_id")
    return f"[coverage] {inv.cluster.gap_type} in doc {doc_id} ({inv.cluster.count} occ)"


def render_markdown(
    *,
    doc_meta: dict[str, Any],
    coverage_summary: dict[str, Any],
    investigations: list[Investigation],
    skipped_clusters: list[GapCluster],
    promotion_results: list[dict[str, Any]] | None = None,
) -> str:
    lines: list[str] = []
    lines.append(
        f"# Coverage investigation: {doc_meta.get('bylaw_name')} "
        f"(doc {doc_meta.get('document_id')})\n"
    )
    lines.append(
        f"_Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')}_\n\n"
    )

    lines.append("## Coverage summary\n")
    lines.append(
        f"- **Grade:** {coverage_summary.get('grade')} "
        f"(mean page overlap {coverage_summary.get('mean_page_overlap')})\n"
        f"- **Pages:** {coverage_summary.get('pages_with_fragments')}"
        f"/{coverage_summary.get('page_count')} with fragments\n"
        f"- **Fragments:** {coverage_summary.get('parsed_fragments')} parsed, "
        f"{coverage_summary.get('uncertain_fragments')} uncertain\n"
        f"- **Tables:** {coverage_summary.get('total_tables')} total, "
        f"{coverage_summary.get('tables_without_cells')} empty\n"
        f"- **Cross-refs:** {coverage_summary.get('resolved_cross_references')}"
        f"/{coverage_summary.get('total_cross_references')} resolved\n\n"
    )

    grouped: dict[str, list[Investigation]] = defaultdict(list)
    for inv in investigations:
        grouped[inv.recommended_action].append(inv)

    lines.append("## Triage table\n\n")
    lines.append(
        "| Action | Gap type | Severity | Count | Confidence | Hypothesis |\n"
    )
    lines.append("|---|---|---|---|---|---|\n")
    for action in _ACTION_ORDER:
        for inv in grouped.get(action, []):
            hyp = inv.hypothesis.replace("\n", " ").replace("|", "\\|")[:120]
            lines.append(
                f"| {action} | `{inv.cluster.gap_type}` | "
                f"{inv.cluster.severity} | {inv.cluster.count} | "
                f"{inv.confidence} | {hyp} |\n"
            )
    lines.append("\n")

    for action in _ACTION_ORDER:
        bucket = grouped.get(action, [])
        if not bucket:
            continue
        lines.append(f"## {_ACTION_LABELS[action]}\n\n")
        for inv in bucket:
            c = inv.cluster
            lines.append(
                f"### `{c.gap_type}` — {c.count} occurrence(s), severity {c.severity}\n\n"
            )
            if c.pages:
                pages_str = (
                    ", ".join(str(p) for p in c.pages[:10])
                    + ("..." if len(c.pages) > 10 else "")
                )
                lines.append(f"**Pages:** {pages_str}\n\n")
            if inv.error:
                lines.append(f"**Investigation failed:** {inv.error}\n\n")
                continue
            lines.append(f"**Hypothesis** ({inv.confidence} confidence): {inv.hypothesis}\n\n")
            if inv.evidence:
                lines.append(f"**Evidence:** {inv.evidence}\n\n")
            if inv.suspected_files:
                lines.append("**Suspected files:**\n")
                for f in inv.suspected_files:
                    lines.append(f"- `{f}`\n")
                lines.append("\n")
            if inv.rationale:
                lines.append(f"**Rationale:** {inv.rationale}\n\n")
            if c.details:
                lines.append("<details><summary>Raw gap details</summary>\n\n")
                for d in c.details:
                    lines.append(f"- {d}\n")
                lines.append("\n</details>\n\n")

    if skipped_clusters:
        lines.append("## Skipped (over --max-clusters limit)\n\n")
        for c in skipped_clusters:
            lines.append(
                f"- `{c.gap_type}` ({c.severity}, {c.count} occurrences)\n"
            )
        lines.append("\n")

    if promotion_results:
        lines.append("## Linear promotion results\n\n")
        lines.append("| Gap type | Status | Issue / Reason |\n")
        lines.append("|---|---|---|\n")
        for r in promotion_results:
            if r["status"] == "created":
                ref = f"[{r['identifier']}]({r['url']})"
            elif r["status"] == "dry-run":
                ref = "_(dry-run, not filed)_"
            elif r["status"] == "skipped-low-confidence":
                ref = "confidence below threshold"
            elif r["status"] == "failed":
                ref = r.get("error", "unknown error")
            else:
                ref = r.get("status", "")
            lines.append(f"| `{r['gap_type']}` | {r['status']} | {ref} |\n")
        lines.append("\n")

    return "".join(lines)


# --------------------------------------------------------------------------- #
# Orchestration                                                               #
# --------------------------------------------------------------------------- #


def promote_to_linear(
    investigations: list[Investigation],
    *,
    doc_meta: dict[str, Any],
    coverage_summary: dict[str, Any],
    team_key: str,
    api_key: str,
    dry_run: bool,
) -> list[dict[str, Any]]:
    """Open one Linear issue per promotable file-issue cluster.

    Returns a list of dicts with at minimum {gap_type, status, ...}.
    `status` is one of: created | skipped-low-confidence | dry-run | failed.

    Imported lazily so the rest of the script never depends on httpx /
    asyncio just because someone runs without --promote-to-linear.
    """
    import asyncio  # noqa: PLC0415

    # The night_manager package lives under scripts/ -- it's not on
    # sys.path by default. Add the project root so the import works
    # whether the user runs the script as `python scripts/...` or via
    # the installed venv.
    project_root = Path(__file__).resolve().parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from scripts.night_manager.linear_client import LinearClient  # noqa: PLC0415

    promotable = [
        inv for inv in investigations
        if inv.recommended_action == "file-issue"
    ]
    if not promotable:
        return []

    results: list[dict[str, Any]] = []

    async def _run() -> None:
        client: LinearClient | None = None
        team_id: str | None = None
        try:
            if not dry_run:
                client = LinearClient(api_key=api_key)
                team_id = await client.get_team_id(team_key)
            for inv in promotable:
                title = build_linear_issue_title(inv, doc_meta)
                body = build_linear_issue_body(
                    inv,
                    doc_meta=doc_meta,
                    coverage_summary=coverage_summary,
                )
                if inv.confidence not in _PROMOTABLE_CONFIDENCE:
                    results.append({
                        "gap_type": inv.cluster.gap_type,
                        "title": title,
                        "status": "skipped-low-confidence",
                    })
                    continue
                if dry_run:
                    results.append({
                        "gap_type": inv.cluster.gap_type,
                        "title": title,
                        "body": body,
                        "status": "dry-run",
                    })
                    continue
                assert client is not None and team_id is not None
                try:
                    created = await client.create_issue(
                        team_id=team_id, title=title, description=body,
                    )
                    results.append({
                        "gap_type": inv.cluster.gap_type,
                        "title": title,
                        "identifier": created["identifier"],
                        "url": created["url"],
                        "status": "created",
                    })
                except Exception as e:
                    results.append({
                        "gap_type": inv.cluster.gap_type,
                        "title": title,
                        "status": "failed",
                        "error": str(e)[:300],
                    })
        finally:
            if client is not None:
                await client.close()

    asyncio.run(_run())
    return results


def _resolve_claude_bin(override: str | None) -> str:
    if override:
        if not Path(override).exists():
            raise SystemExit(f"--claude-bin not found: {override}")
        return override
    found = shutil.which("claude")
    if not found:
        raise SystemExit(
            "could not locate `claude` on PATH; pass --claude-bin or install Claude Code"
        )
    return found


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Investigate coverage gaps in an ingested bylaw document.",
    )
    parser.add_argument("document_id", type=int)
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Markdown report output path (default: investigations/coverage-<id>-<ts>.md)",
    )
    parser.add_argument(
        "--severity",
        default="high,medium",
        help="Comma-separated severities to investigate (high,medium,low). Default: high,medium",
    )
    parser.add_argument(
        "--max-clusters",
        type=int,
        default=20,
        help="Cap on number of clusters to send to the LLM (default: 20).",
    )
    parser.add_argument(
        "--deep",
        action="store_true",
        help="Investigate every gap individually instead of clustering by type.",
    )
    parser.add_argument(
        "--skip-llm",
        action="store_true",
        help="Skip the LLM call; produce a structural report only.",
    )
    parser.add_argument("--claude-bin", default=None)
    parser.add_argument("--db-url", default=None)
    parser.add_argument(
        "--coverage-json",
        type=Path,
        default=None,
        help="Use this pre-computed coverage JSON instead of invoking layer1.",
    )
    parser.add_argument(
        "--promote-to-linear",
        action="store_true",
        help=(
            "After investigation, open Linear issues for clusters whose "
            "recommended_action is `file-issue` and confidence is medium/high. "
            "Requires LINEAR_API_KEY in the environment."
        ),
    )
    parser.add_argument(
        "--linear-team",
        default="ABS",
        help="Linear team key to file issues under (default: ABS).",
    )
    parser.add_argument(
        "--linear-dry-run",
        action="store_true",
        help="With --promote-to-linear, print what would be filed without calling the API.",
    )
    args = parser.parse_args(argv)

    severities = {s.strip() for s in args.severity.split(",") if s.strip()}
    project_dir = Path(__file__).resolve().parent.parent

    print(f"[1/4] Loading coverage report for doc {args.document_id}...", file=sys.stderr)
    if args.coverage_json:
        coverage = json.loads(args.coverage_json.read_text())
    else:
        coverage = run_verify_coverage(args.document_id, db_url=args.db_url)

    summary = coverage.get("summary", {})
    doc_meta = {
        "document_id": coverage.get("document_id"),
        "municipality": coverage.get("municipality"),
        "bylaw_name": coverage.get("bylaw_name"),
    }
    raw_gaps = coverage.get("gaps", [])
    print(
        f"      grade={summary.get('grade')} gaps={len(raw_gaps)} "
        f"(filtering to severities={sorted(severities)})",
        file=sys.stderr,
    )

    filtered = filter_gaps(raw_gaps, severities=severities)
    clusters = cluster_gaps(filtered, deep=args.deep)
    print(f"[2/4] Built {len(clusters)} cluster(s) from {len(filtered)} gap(s).", file=sys.stderr)

    skipped: list[GapCluster] = []
    if len(clusters) > args.max_clusters:
        skipped = clusters[args.max_clusters :]
        clusters = clusters[: args.max_clusters]
        print(
            f"      capped at --max-clusters={args.max_clusters}; "
            f"{len(skipped)} cluster(s) deferred.",
            file=sys.stderr,
        )

    investigations: list[Investigation] = []
    if not clusters:
        print("[3/4] No clusters to investigate — nothing to do.", file=sys.stderr)
    elif args.skip_llm:
        print("[3/4] --skip-llm set; rendering structural report only.", file=sys.stderr)
        for c in clusters:
            investigations.append(
                Investigation(
                    cluster=c,
                    hypothesis="(skipped: --skip-llm)",
                    recommended_action="manual-review",
                    confidence="low",
                )
            )
    else:
        claude_bin = _resolve_claude_bin(args.claude_bin)
        print(
            f"[3/4] Investigating {len(clusters)} cluster(s) via {claude_bin}...",
            file=sys.stderr,
        )
        for i, cluster in enumerate(clusters, start=1):
            audit_payload: dict[str, Any] | None = None
            if cluster.representative_page is not None:
                try:
                    audit_payload = run_audit_page(
                        args.document_id,
                        cluster.representative_page,
                        db_url=args.db_url,
                    )
                except RuntimeError as e:
                    print(f"      audit-page failed for {cluster.gap_type}: {e}", file=sys.stderr)
            t0 = time.monotonic()
            inv = investigate_with_claude(
                cluster,
                audit_payload,
                summary,
                doc_meta,
                claude_bin=claude_bin,
                project_dir=project_dir,
            )
            dt = time.monotonic() - t0
            tag = "ok" if inv.succeeded else f"FAIL ({inv.error})"
            print(
                f"      [{i}/{len(clusters)}] {cluster.gap_type} "
                f"({cluster.count} occ, {cluster.severity}) -> "
                f"{inv.recommended_action} [{tag}] {dt:.1f}s",
                file=sys.stderr,
            )
            investigations.append(inv)

    promotion_results: list[dict[str, Any]] = []
    if args.promote_to_linear:
        api_key = os.environ.get("LINEAR_API_KEY", "").strip()
        if not args.linear_dry_run and not api_key:
            print(
                "[!] --promote-to-linear requires LINEAR_API_KEY in the "
                "environment (or pass --linear-dry-run to preview).",
                file=sys.stderr,
            )
            return 2
        print(
            f"[5/5] Promoting file-issue clusters to Linear team {args.linear_team}"
            + (" (dry-run)" if args.linear_dry_run else "")
            + "...",
            file=sys.stderr,
        )
        promotion_results = promote_to_linear(
            investigations,
            doc_meta=doc_meta,
            coverage_summary=summary,
            team_key=args.linear_team,
            api_key=api_key,
            dry_run=args.linear_dry_run,
        )
        for r in promotion_results:
            extra = (
                r.get("identifier") or r.get("error") or ""
            )
            print(
                f"      {r['gap_type']}: {r['status']} {extra}".rstrip(),
                file=sys.stderr,
            )

    report = render_markdown(
        doc_meta=doc_meta,
        coverage_summary=summary,
        investigations=investigations,
        skipped_clusters=skipped,
        promotion_results=promotion_results,
    )

    out_path = args.out
    if out_path is None:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        out_dir = project_dir / "investigations"
        out_dir.mkdir(exist_ok=True)
        out_path = out_dir / f"coverage-{args.document_id}-{ts}.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report)
    print(f"[4/4] Wrote {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
