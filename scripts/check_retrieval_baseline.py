#!/usr/bin/env python3
"""
Fail when ``evals/retrieval/BASELINE.json`` no longer describes the retrieval
code committed beside it (ABS-502).

Why this exists
---------------
``BASELINE.json`` was written once at ABS-486 and then went stale in silence.
ABS-478 and ABS-488 both moved retrieval without anyone re-recording it, so the
file read Recall@10 = 0.1029 while dev was actually at 0.1618. Two things
followed, and both are the kind of error an eval exists to prevent:

* ABS-494 argued its case ("+0.34, zero regressions") against a control that
  had already moved.
* ABS-488 shipped a ranking inversion — container prose banking path weight on
  a child clause — that nothing caught. ABS-492 found it by accident, doing
  unrelated work. A baseline regenerated at ABS-488's merge would have shown it
  the same day.

A stale baseline is worse than no baseline: it does not merely fail to detect a
regression, it actively certifies one. So this check makes the staleness
structurally impossible rather than a thing a reviewer has to remember.

What the verdict is actually computed from
------------------------------------------
The obvious implementation is "compare the mtime/commit-date of BASELINE.json
against the newest commit touching the retrieval paths". This checker reports
those commits — a human reading a failure wants to know *which* change is
unaccounted for — but it does not decide the verdict from them, for one
concrete reason: eb613cf touched ``retrieval/service.py`` to reword a comment.
A gate that fails on a reworded comment gets acknowledged reflexively within a
week, and an acknowledgement habit is the same failure this ticket is closing,
one level up.

So the verdict comes from a **significant-content fingerprint**: every watched
file is normalised (comments and docstrings stripped from Python, JSON reduced
to its graded subset and canonicalised) and hashed. The digest recorded in
BASELINE.json at measurement time is compared against the digest of the working
tree now. That is strictly sharper than a commit comparison in three ways:

1. Prose-only edits do not fire. Behaviour-bearing edits always do.
2. It sees *uncommitted* edits, so the gate answers before the commit, not
   after the merge.
3. It survives rebases, cherry-picks and squashes, which move commit identity
   without moving code.

The normalisation is deliberately interpreter-independent: it deletes comment
and docstring *source spans* and drops blank lines, rather than serialising
tokens, because ``tokenize`` splits f-strings into several tokens on 3.12+ and
one token on 3.11 — a fingerprint that disagreed with itself between the local
venv (3.12) and CI (3.11) would fail every run for a reason no one could act on.

Usage
-----
  python scripts/check_retrieval_baseline.py            # verdict + exit code
  python scripts/check_retrieval_baseline.py --json     # machine-readable
  python scripts/check_retrieval_baseline.py --acknowledge "reason"

Exit codes: 0 fresh (or drift explicitly acknowledged), 1 stale, 2 the check
itself is misconfigured (a watched path has disappeared, the baseline is
unreadable) — which is never a pass.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import subprocess
import sys
import tokenize
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE = REPO_ROOT / "evals" / "retrieval" / "BASELINE.json"

#: Bumped only when the normalisation below changes shape. A recorded digest
#: carrying a different algorithm cannot be compared and reads as stale.
ALGORITHM = "significant-content-v1"

#: The single command that clears a stale verdict. Named once, quoted by every
#: failure path, so the message can never drift from the Makefile.
REGENERATE_COMMAND = "make eval-retrieval-baseline"

#: Files whose content can move a ranking, and therefore invalidate a recorded
#: Recall@k. Globs are resolved fresh on every run, so a new module dropped
#: into the retrieval package is watched the day it lands rather than the day
#: someone remembers to add it here.
WATCHED_PATTERNS: tuple[tuple[str, str], ...] = (
    (
        "mcp/bylaw_retrieval/retrieval/**/*.py",
        "scoring, channel fusion, zone-scope binding, the table channel",
    ),
    (
        "src/layer1/pipeline/hierarchy.py",
        "the ancestor chain every context and binding lookup walks",
    ),
    (
        "src/layer1/pipeline/citation_repath.py",
        "citation_path construction — ABS-488 moved the baseline from here",
    ),
    (
        "src/layer1/pipeline/corpus_repath.py",
        "the corpus-wide repath driver",
    ),
    (
        "scripts/repath_citation_paths.py",
        "the repath entry point run against the dev corpus",
    ),
    (
        "scripts/eval_retrieval_recall.py",
        "the harness itself — it defines what the number means",
    ),
    (
        "evals/retrieval/queries.json",
        "the graded questions and their labels",
    ),
)

#: For a JSON file, fingerprint only this top-level key. ``queries.json`` also
#: carries a ``provenance`` header whose ``review_status`` a human spot-check
#: flips; that flip changes no measurement and must not fail the gate.
JSON_SUBSET_KEY: dict[str, str] = {"evals/retrieval/queries.json": "queries"}


class CheckError(RuntimeError):
    """The check cannot be run honestly — never reported as a pass."""


# ----------------------------------------------------------------------
# Normalisation
# ----------------------------------------------------------------------


def _docstring_spans(text: str) -> set[tuple[int, int]]:
    """1-indexed inclusive line spans of module/class/function docstrings.

    Only ``body[0]`` of a scope counts, so a string used as a value is left
    alone. This repo writes essay-length docstrings; an edit to one changes no
    ranking and must not fire the gate.

    A docstring sharing a line with code (``def f(): "doc"``) is *not* dropped:
    the unit of removal is a whole line, so dropping that one would take the
    signature with it and hide a rename from the fingerprint.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError as error:  # pragma: no cover - a file that cannot parse
        raise CheckError(f"cannot parse: {error}") from error

    lines = text.splitlines()
    spans: set[tuple[int, int]] = set()
    scopes = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    for node in ast.walk(tree):
        if not isinstance(node, scopes):
            continue
        body = getattr(node, "body", [])
        if not body:
            continue
        first = body[0]
        if not (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            continue
        start, end = first.lineno, first.end_lineno or first.lineno
        if start - 1 >= len(lines) or end - 1 >= len(lines):  # pragma: no cover
            continue
        before = lines[start - 1][: first.col_offset]
        after = lines[end - 1][first.end_col_offset or 0 :]
        if before.strip() or after.strip():
            continue
        spans.add((start, end))
    return spans


def _comment_spans(text: str) -> dict[int, int]:
    """Map of 1-indexed line -> column at which a ``#`` comment starts."""
    starts: dict[int, int] = {}
    try:
        tokens = tokenize.generate_tokens(io.StringIO(text).readline)
        for token in tokens:
            if token.type == tokenize.COMMENT:
                line, column = token.start
                starts[line] = min(column, starts.get(line, column))
    except (tokenize.TokenError, IndentationError, SyntaxError) as error:
        raise CheckError(f"cannot tokenize: {error}") from error
    return starts


def normalise_python(text: str) -> str:
    """Strip comments, docstrings and blank lines; keep everything else verbatim.

    Operates on source *spans* rather than on a token stream so the result does
    not depend on the running interpreter's tokenizer (see the module docstring).
    """
    drop_lines: set[int] = set()
    for start, end in _docstring_spans(text):
        drop_lines.update(range(start, end + 1))
    comment_at = _comment_spans(text)

    kept: list[str] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if number in drop_lines:
            continue
        if number in comment_at:
            line = line[: comment_at[number]]
        line = line.rstrip()
        if not line:
            continue
        kept.append(line)
    return "\n".join(kept)


def normalise_json(text: str, subset_key: str | None) -> str:
    """Canonicalise JSON so reindentation and key order cannot move the digest."""
    try:
        document = json.loads(text)
    except json.JSONDecodeError as error:
        raise CheckError(f"cannot parse JSON: {error}") from error
    if subset_key is not None:
        if not isinstance(document, dict) or subset_key not in document:
            raise CheckError(f"expected a top-level {subset_key!r} key")
        document = document[subset_key]
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(payload: str) -> str:
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _normalise_file(path: Path, relative: str) -> str:
    text = path.read_text(encoding="utf-8")
    try:
        if path.suffix == ".py":
            return normalise_python(text)
        if path.suffix == ".json":
            return normalise_json(text, JSON_SUBSET_KEY.get(relative))
    except CheckError as error:
        raise CheckError(f"{relative}: {error}") from error
    return text


# ----------------------------------------------------------------------
# Fingerprint
# ----------------------------------------------------------------------


def watched_files(repo_root: Path) -> list[str]:
    """Repo-relative paths of every watched file, sorted.

    A literal (non-glob) pattern that resolves to nothing is an error, not an
    empty set: the watch list has gone out of date with the tree, and a check
    watching a file that no longer exists would pass forever.
    """
    found: set[str] = set()
    for pattern, _why in WATCHED_PATTERNS:
        matches = sorted(repo_root.glob(pattern))
        if not matches and "*" not in pattern:
            raise CheckError(
                f"watched path {pattern!r} does not exist. The retrieval code moved "
                "and WATCHED_PATTERNS in scripts/check_retrieval_baseline.py was not "
                "updated with it."
            )
        for match in matches:
            if match.is_file():
                found.add(match.relative_to(repo_root).as_posix())
    return sorted(found)


def retrieval_fingerprint(repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    """Fingerprint every watched file in the working tree at *repo_root*."""
    files = {
        relative: _digest(_normalise_file(repo_root / relative, relative))
        for relative in watched_files(repo_root)
    }
    combined = "\n".join(f"{name}\x00{value}" for name, value in sorted(files.items()))
    return {
        "_comment": (
            "Fingerprint of the files whose content can move a ranking, taken when "
            "this baseline was measured. Python is normalised by stripping comments, "
            "docstrings and blank lines; JSON is canonicalised. "
            "scripts/check_retrieval_baseline.py compares it against the working tree "
            f"and fails the gate when they differ — run `{REGENERATE_COMMAND}`."
        ),
        "algorithm": ALGORITHM,
        "measured_at_commit": _head_commit(repo_root),
        "digest": _digest(combined),
        "files": files,
        "acknowledged_drift": None,
    }


# ----------------------------------------------------------------------
# Git context (reporting only — never the verdict)
# ----------------------------------------------------------------------


def _git(repo_root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _head_commit(repo_root: Path) -> str | None:
    return _git(repo_root, "rev-parse", "HEAD")


def commits_since(repo_root: Path, commit: str | None) -> list[str]:
    """Watched-path commits since *commit* that did not re-record the baseline.

    A commit that changed retrieval *and* rewrote BASELINE.json in the same
    breath is exactly what this gate asks for, so it is filtered out — leaving
    the list to name only the changes nothing accounted for. That includes the
    commit carrying the baseline itself, which is always one commit ahead of
    the HEAD the measurement was taken at.

    Best effort and purely informational: a checkout without git history, or a
    recorded commit that was rebased away, yields an empty list and the
    fingerprint verdict stands on its own.
    """
    if not commit:
        return []
    if _git(repo_root, "cat-file", "-e", f"{commit}^{{commit}}") is None:
        return []
    paths = [pattern.split("**")[0].rstrip("/") for pattern, _why in WATCHED_PATTERNS]
    output = _git(repo_root, "log", "--format=%h %s", f"{commit}..HEAD", "--", *paths)
    if not output:
        return []
    rerecorded = _git(
        repo_root,
        "log",
        "--format=%h",
        f"{commit}..HEAD",
        "--",
        "evals/retrieval/BASELINE.json",
    )
    accounted = set((rerecorded or "").split())
    return [
        line
        for line in output.splitlines()
        if line.strip() and line.split(" ", 1)[0] not in accounted
    ]


# ----------------------------------------------------------------------
# Verdict
# ----------------------------------------------------------------------

FRESH = "fresh"
ACKNOWLEDGED = "acknowledged"
STALE = "stale"


@dataclass(frozen=True)
class Verdict:
    """The answer, plus everything a human needs to act on it."""

    verdict: str
    reason: str
    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    modified: tuple[str, ...] = ()
    commits: tuple[str, ...] = ()
    recorded_commit: str | None = None
    acknowledgement: dict[str, Any] | None = None

    @property
    def ok(self) -> bool:
        return self.verdict in (FRESH, ACKNOWLEDGED)

    @property
    def drifted(self) -> tuple[str, ...]:
        return tuple(sorted({*self.added, *self.removed, *self.modified}))

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "reason": self.reason,
            "added": list(self.added),
            "removed": list(self.removed),
            "modified": list(self.modified),
            "commits_since_baseline": list(self.commits),
            "recorded_commit": self.recorded_commit,
            "acknowledgement": self.acknowledgement,
            "regenerate_command": REGENERATE_COMMAND,
        }


def evaluate_freshness(baseline: dict[str, Any], current: dict[str, Any]) -> Verdict:
    """Compare a recorded fingerprint block against a freshly computed one."""
    recorded = baseline.get("retrieval_fingerprint")
    if not isinstance(recorded, dict):
        return Verdict(
            verdict=STALE,
            reason=(
                "this baseline carries no retrieval fingerprint, so nothing records "
                "which retrieval code it was measured against"
            ),
        )

    recorded_commit = recorded.get("measured_at_commit")
    if recorded.get("algorithm") != ALGORITHM:
        return Verdict(
            verdict=STALE,
            reason=(
                f"fingerprint algorithm {recorded.get('algorithm')!r} predates the "
                f"current {ALGORITHM!r} and cannot be compared"
            ),
            recorded_commit=recorded_commit,
        )

    recorded_files: dict[str, str] = recorded.get("files") or {}
    current_files: dict[str, str] = current["files"]
    added = tuple(sorted(set(current_files) - set(recorded_files)))
    removed = tuple(sorted(set(recorded_files) - set(current_files)))
    modified = tuple(
        sorted(
            name
            for name in set(recorded_files) & set(current_files)
            if recorded_files[name] != current_files[name]
        )
    )

    if recorded.get("digest") == current["digest"] and not (added or removed or modified):
        return Verdict(
            verdict=FRESH,
            reason="every watched file is byte-for-byte the code this baseline was measured against",
            recorded_commit=recorded_commit,
        )

    acknowledgement = recorded.get("acknowledged_drift")
    if (
        isinstance(acknowledgement, dict)
        and acknowledgement.get("digest") == current["digest"]
        and str(acknowledgement.get("reason") or "").strip()
    ):
        return Verdict(
            verdict=ACKNOWLEDGED,
            reason=(
                "the retrieval code moved and the drift is explicitly acknowledged: "
                f"{acknowledgement['reason']}"
            ),
            added=added,
            removed=removed,
            modified=modified,
            recorded_commit=recorded_commit,
            acknowledgement=acknowledgement,
        )

    return Verdict(
        verdict=STALE,
        reason=(
            f"{len(added) + len(removed) + len(modified)} watched file(s) changed since "
            "this baseline was measured, so its numbers describe code that no longer exists"
        ),
        added=added,
        removed=removed,
        modified=modified,
        recorded_commit=recorded_commit,
    )


def check(baseline_path: Path, repo_root: Path = REPO_ROOT) -> Verdict:
    """Full check: read the baseline, fingerprint the tree, add git context."""
    try:
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise CheckError(f"no baseline at {baseline_path}") from error
    except json.JSONDecodeError as error:
        raise CheckError(f"{baseline_path} is not valid JSON: {error}") from error

    verdict = evaluate_freshness(baseline, retrieval_fingerprint(repo_root))
    commits = commits_since(repo_root, verdict.recorded_commit)
    if not commits:
        return verdict
    return Verdict(
        verdict=verdict.verdict,
        reason=verdict.reason,
        added=verdict.added,
        removed=verdict.removed,
        modified=verdict.modified,
        commits=tuple(commits),
        recorded_commit=verdict.recorded_commit,
        acknowledgement=verdict.acknowledgement,
    )


# ----------------------------------------------------------------------
# Acknowledgement
# ----------------------------------------------------------------------


def acknowledge(baseline_path: Path, reason: str, repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    """Record, in BASELINE.json, a decision to carry the current drift.

    The escape hatch is deliberately narrow. It pins the *exact* digest it was
    granted for, so the next edit to any watched file fails the gate again, and
    it lands as a reviewable line in the diff rather than as a flag someone
    passes in a shell.
    """
    reason = reason.strip()
    if not reason:
        raise CheckError("an acknowledgement needs a reason")

    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    recorded = baseline.get("retrieval_fingerprint")
    if not isinstance(recorded, dict):
        raise CheckError(
            "this baseline has no fingerprint to acknowledge drift against — run "
            f"`{REGENERATE_COMMAND}` instead"
        )

    current = retrieval_fingerprint(repo_root)
    drift = evaluate_freshness(baseline, current)
    if recorded.get("digest") == current["digest"]:
        raise CheckError("nothing to acknowledge: the baseline is already fresh")

    acknowledgement = {
        "_comment": (
            "A deliberate decision to keep a baseline measured against slightly older "
            "retrieval code. Valid only for the exact digest below; the next change to "
            "any watched file fails the gate again."
        ),
        "digest": current["digest"],
        "reason": reason,
        "acknowledged_commit": current["measured_at_commit"],
        "drifted_files": list(drift.drifted),
    }
    recorded["acknowledged_drift"] = acknowledgement
    baseline_path.write_text(
        json.dumps(baseline, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return acknowledgement


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------


def _print_report(verdict: Verdict, baseline_path: Path) -> None:
    if verdict.ok:
        label = "FRESH" if verdict.verdict == FRESH else "ACKNOWLEDGED DRIFT"
        print(f"{label}: {verdict.reason}")
        if verdict.verdict == ACKNOWLEDGED:
            for name in verdict.drifted:
                print(f"    drifted  {name}")
        if verdict.commits:
            since = verdict.recorded_commit[:7] if verdict.recorded_commit else "the recording"
            tail = (
                ", none of them changing behaviour-bearing content"
                if verdict.verdict == FRESH
                else ""
            )
            print(
                f"\n{len(verdict.commits)} commit(s) touched the watched paths since "
                f"{since}{tail}:"
            )
            for line in verdict.commits:
                print(f"    {line}")
        return

    print(f"STALE: {baseline_path} — {verdict.reason}", file=sys.stderr)
    for name in verdict.added:
        print(f"    added     {name}", file=sys.stderr)
    for name in verdict.removed:
        print(f"    removed   {name}", file=sys.stderr)
    for name in verdict.modified:
        print(f"    modified  {name}", file=sys.stderr)
    if verdict.commits:
        print(
            f"\nCommits touching retrieval since "
            f"{verdict.recorded_commit[:7] if verdict.recorded_commit else 'the recording'}:",
            file=sys.stderr,
        )
        for line in verdict.commits:
            print(f"    {line}", file=sys.stderr)
    print(
        "\nA Recall@k measured against code that no longer exists cannot tell a "
        "regression from an improvement. Re-measure it:\n"
        f"\n    {REGENERATE_COMMAND}\n"
        "\n(needs the dev corpus; see evals/retrieval/README.md). Then commit the "
        "BASELINE.json diff with the change that moved it.\n"
        "\nIf — and only if — the change genuinely cannot move a ranking, record that "
        "decision instead:\n"
        "\n    python scripts/check_retrieval_baseline.py --acknowledge "
        '"why this cannot move a ranking"\n',
        file=sys.stderr,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--json", action="store_true", help="Emit the verdict as JSON.")
    parser.add_argument(
        "--acknowledge",
        metavar="REASON",
        help=(
            "Record a decision to carry the current drift instead of re-measuring. "
            "Valid only for the exact current digest."
        ),
    )
    args = parser.parse_args(argv)

    try:
        if args.acknowledge is not None:
            acknowledgement = acknowledge(args.baseline, args.acknowledge, args.repo_root)
            print(f"Acknowledged drift at {acknowledgement['digest'][:14]}…")
            print(f"  reason: {acknowledgement['reason']}")
            print(f"Wrote {args.baseline} — commit it with the change it covers.")
            return 0
        verdict = check(args.baseline, args.repo_root)
    except CheckError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(verdict.to_dict(), indent=2, ensure_ascii=False))
    else:
        _print_report(verdict, args.baseline)
    return 0 if verdict.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
