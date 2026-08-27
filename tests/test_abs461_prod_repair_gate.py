"""The drift gate and the redaction filter in scripts/apply-abs461-prod-repair.sh (ABS-465).

ABS-465 says the production dry run "must print exactly the four splits in the
assessment doc" and, if it prints anything else, **stop**. Left to a human that
check is four fragment-id pairs compared against a markdown table, which is the
kind of check that gets waved through at the end of a long procedure. The gate
makes it mechanical; these tests make the gate trustworthy.

Every case here is a way production could have moved since the assessment was
measured — a fifth split appearing, a fragment renumbered, the change coming
out a different size, the repair declining to place a clause. Each must stop
the run, because each means the corpus is no longer the thing the repair was
reasoned about.
"""
from __future__ import annotations

import subprocess
import urllib.parse
from pathlib import Path

import pytest

GATE = Path(__file__).resolve().parents[1] / "scripts" / "apply-abs461-prod-repair.sh"

# Transcript of the real production dry run, 2026-08-27, trimmed to the lines
# the gate reads. Fragment ids and the summary line are verbatim.
SPLIT_LINE = "INFO repair_page_break_splits: doc 4: would join fragment {head} + {tail}"
ASSESSED_SPLITS = [(5791, 5792), (6070, 6071), (6393, 6394), (7121, 7122)]
ASSESSED_SUMMARY = (
    "page-break splits: 4 joined, 2 phantom section(s) removed, "
    "10 citation_path(s) rewritten, 0 unresolved, 0 embedding(s) invalidated "
    "elapsed_s=3.0"
)


def transcript(
    splits: list[tuple[int, int]] | None = None,
    summary: str = ASSESSED_SUMMARY,
    extra: str = "",
) -> str:
    lines = [
        SPLIT_LINE.format(head=head, tail=tail)
        for head, tail in (ASSESSED_SPLITS if splits is None else splits)
    ]
    if extra:
        lines.append(extra)
    lines.append(summary)
    return "\n".join(lines) + "\n"


def run_gate(tmp_path: Path, text: str) -> subprocess.CompletedProcess[str]:
    path = tmp_path / "dry-run.txt"
    path.write_text(text)
    return subprocess.run(
        ["bash", str(GATE), "--gate", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_assessed_dry_run_passes(tmp_path):
    result = run_gate(tmp_path, transcript())
    assert result.returncode == 0, result.stderr
    assert "matches the assessed state" in result.stderr


def test_an_extra_split_stops_the_run(tmp_path):
    # A fifth break means the parser found damage the assessment never saw.
    result = run_gate(tmp_path, transcript([*ASSESSED_SPLITS, (8001, 8002)]))
    assert result.returncode != 0
    assert "no longer matches the assessed state" in result.stderr


def test_a_missing_split_stops_the_run(tmp_path):
    # Someone else may have already repaired part of the corpus.
    result = run_gate(tmp_path, transcript(ASSESSED_SPLITS[:3]))
    assert result.returncode != 0
    assert "no longer matches the assessed state" in result.stderr


def test_a_renumbered_fragment_stops_the_run(tmp_path):
    # Same count, same shape, different rows — which is exactly what a
    # re-ingest would leave behind, and the case a count check would miss.
    moved = [(5791, 5792), (6070, 6071), (6393, 6394), (9121, 9122)]
    result = run_gate(tmp_path, transcript(moved))
    assert result.returncode != 0
    assert "no longer matches the assessed state" in result.stderr


def test_a_differently_sized_change_stops_the_run(tmp_path):
    # The four splits can be right while the blast radius is not: more paths
    # rewritten means more clauses hung off the phantoms than were assessed.
    summary = ASSESSED_SUMMARY.replace("10 citation_path(s)", "31 citation_path(s)")
    result = run_gate(tmp_path, transcript(summary=summary))
    assert result.returncode != 0
    assert "not the size the assessment measured" in result.stderr


@pytest.mark.parametrize(
    "warning",
    [
        (
            "WARNING repair_page_break_splits:     phantom 'Part V > 9' has no "
            "preceding section to graft onto — text joined, paths LEFT AS-IS "
            "for manual review"
        ),
        (
            "WARNING repair_page_break_splits:       7125: SKIPPED, "
            "'Part V > 198 > [Side Setback Requirements] > (b)' already exists"
        ),
    ],
)
def test_a_clause_the_repair_could_not_place_stops_the_run(tmp_path, warning):
    # The repair refusing to guess is correct behaviour. Applying anyway would
    # leave orphaned clauses behind with no phantom left to find them under.
    result = run_gate(tmp_path, transcript(extra=warning))
    assert result.returncode != 0
    assert "could not place every clause" in result.stderr


def test_an_empty_transcript_stops_the_run(tmp_path):
    # A dry run that produced nothing is not a dry run that found nothing.
    result = run_gate(tmp_path, "")
    assert result.returncode != 0
    assert "missing or empty" in result.stderr


def test_the_gate_never_writes(tmp_path):
    # --gate is the mode the tests exercise; it must not be a path into the
    # apply branch, or these tests would be reaching for production.
    result = run_gate(tmp_path, transcript())
    assert result.returncode == 0
    assert "Opening tunnel" not in result.stderr
    assert list(tmp_path.iterdir()) == [tmp_path / "dry-run.txt"]


# --- the redaction filter -------------------------------------------------
#
# Everything the repair prints passes through this on its way to the operator's
# terminal, because a failed connection prints the DSN. It used to be
# `sed "s/${PG_PASSWORD}/***/g"`, and a generated Postgres password is exactly
# the kind of string that breaks a sed pattern: '/' ends the s/// delimiter,
# '.' matches anything, '&' expands to the whole match, '\' escapes. The first
# is a hard error, and a hard error here under `set -euo pipefail` used to
# abort the run and throw away the repair's entire output — on the --apply
# path, that discards the revert sidecar path after the write has committed.
# So these cases are not hypothetical unicode trivia; they are the reason the
# filter is Python and not sed.

PASSWORDS_THAT_BREAK_SED = [
    "a/b/c",  # closes sed's own delimiter — the confirmed repro
    "pw.with.dots",  # '.' would match any character
    "amp&sand",  # '&' expands to the whole match in the replacement
    "back\\slash",  # '\' escapes whatever follows it
    "brack[et]s*+?^$",  # a character class, and every quantifier
    "plain-secret",
]


def run_redact(text: str, password: str, home: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(GATE), "--redact"],
        input=text,
        capture_output=True,
        text=True,
        check=False,
        # A deliberately bare environment: the filter must not depend on
        # anything the operator's shell happens to be carrying.
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(home), "PG_PASSWORD": password},
    )


@pytest.mark.parametrize("password", PASSWORDS_THAT_BREAK_SED)
def test_the_password_is_redacted_whatever_characters_it_carries(tmp_path, password):
    encoded = urllib.parse.quote(password, safe="")
    transcript_text = (
        f"could not connect to postgresql+psycopg://layer1:{encoded}@127.0.0.1:15442/layer1\n"
        f"psql: FATAL: password authentication failed (tried {password})\n"
        "page-break splits: 4 joined\n"
    )

    result = run_redact(transcript_text, password, tmp_path)

    assert result.returncode == 0, result.stderr
    assert password not in result.stdout
    assert encoded not in result.stdout
    # Redacting is not the same as swallowing: the operator still needs to be
    # able to read the transcript that told them something went wrong.
    assert "page-break splits: 4 joined" in result.stdout
    assert result.stdout.count("***") == 2


def test_redaction_never_swallows_the_transcript(tmp_path):
    # The failure this guards is not "the password leaked" but "the run died
    # in the filter and took the sidecar path with it". Output in, output out.
    text = "revert sidecar written to /home/op/abs461-prod-sidecars/x.json\n"
    result = run_redact(text, "s/l/a/s/h/e/s", tmp_path)
    assert result.returncode == 0
    assert result.stdout == text
