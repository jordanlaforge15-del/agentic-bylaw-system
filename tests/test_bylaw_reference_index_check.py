"""ABS-464: make `build_bylaw_reference_index.py --check` an automated gate.

ABS-463 shipped the reference index and a `--check` mode to keep it honest.
Two things were wrong with that guard:

1. `--check` compared only the *per-reference resolutions* (`_comparable()`
   deliberately strips row ids and counts), then printed "snapshot is current".
   The snapshot's provenance — `document_id`, `source_fragment_count`,
   `reference_count`, the fields that record *which corpus it came from* — was
   never compared. With the index at 4341 fragments and the post-ABS-461 corpus
   at 4337, `--check` still exited 0.
2. Nothing ran `--check`. Not CI, not the Makefile, not a test. Grepping the
   repo found only prose mentions in docstrings.

This module fixes (2), and proves (1) actually bites.

Two tiers, because the guard has to be useful in both worlds:

* **Offline** — `provenance_drift()` is pure (two dicts in, list of strings
  out), so the drift rule itself is asserted with no database at all. These
  run in CI, in a fresh worktree, on a plane.
* **Live** — the end-to-end `--check` path, which needs the real 4,300-fragment
  Halifax ingest. Those tests skip cleanly when Postgres is unreachable or the
  Regional Centre document row is absent, so `make test` stays runnable
  offline, but they fail loudly wherever the corpus *is* present and the
  snapshot has gone stale.

The index-vs-prompts axis is covered by `tests/test_eval_bylaw_references.py`
and `web/e2e/functional/abs463-bylaw-reference-validation.spec.ts`. Neither
touches Postgres, so neither can see index-vs-corpus drift — which is precisely
the axis that moves on re-ingest. That gap is what this file closes.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

import scripts.build_bylaw_reference_index as index_script
from scripts.build_bylaw_reference_index import (
    BYLAW_NAME,
    DEFAULT_DB_URL,
    INDEX_FILE,
    PROVENANCE_FIELDS,
    main,
    provenance_drift,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Offline: the drift rule itself
# ---------------------------------------------------------------------------


def _snapshot(**overrides: Any) -> dict[str, Any]:
    """A minimal index dict shaped like the real snapshot."""
    base = {
        "bylaw_name": BYLAW_NAME,
        "document_id": 4,
        "source_fragment_count": 4337,
        "reference_count": 101,
        "references": {},
    }
    base.update(overrides)
    return base


def test_provenance_drift_is_silent_when_the_snapshot_matches_the_corpus():
    assert provenance_drift(_snapshot(), _snapshot()) == []


def test_provenance_drift_names_both_fragment_counts():
    """The exact ABS-464 scenario: index says 4341, corpus says 4337.

    The message has to carry both numbers — "stale" alone does not tell the
    next person whether the corpus grew, shrank, or was replaced.
    """
    drift = provenance_drift(
        _snapshot(source_fragment_count=4341),
        _snapshot(source_fragment_count=4337),
    )
    assert len(drift) == 1
    assert "source_fragment_count" in drift[0]
    assert "4341" in drift[0], "the committed (stale) count must be named"
    assert "4337" in drift[0], "the live count must be named"


def test_provenance_drift_catches_a_changed_document_id():
    """A re-ingest that lands as a new document row is not the same corpus."""
    drift = provenance_drift(_snapshot(document_id=4), _snapshot(document_id=9))
    assert len(drift) == 1
    assert "document_id" in drift[0] and "4" in drift[0] and "9" in drift[0]


def test_provenance_drift_catches_a_changed_reference_count():
    drift = provenance_drift(_snapshot(reference_count=101), _snapshot(reference_count=102))
    assert len(drift) == 1
    assert "reference_count" in drift[0]


def test_provenance_drift_reports_every_drifted_field_at_once():
    """Don't make the operator re-run to discover the second mismatch."""
    drift = provenance_drift(
        _snapshot(document_id=4, source_fragment_count=4341, reference_count=101),
        _snapshot(document_id=9, source_fragment_count=4337, reference_count=102),
    )
    assert len(drift) == len(PROVENANCE_FIELDS)


def test_provenance_drift_treats_an_absent_field_as_drift():
    """A snapshot predating the provenance fields must not silently pass."""
    stale = _snapshot()
    del stale["source_fragment_count"]
    drift = provenance_drift(stale, _snapshot())
    assert len(drift) == 1
    assert "source_fragment_count" in drift[0]


def test_the_committed_snapshot_is_internally_consistent():
    """`reference_count` must describe the entries actually in the file.

    Cheap, DB-free, and catches a hand-edited index — the failure mode that
    made ABS-464 possible in the first place.
    """
    committed = json.loads(INDEX_FILE.read_text())
    assert committed["reference_count"] == len(committed["references"])
    assert isinstance(committed["source_fragment_count"], int)
    assert committed["source_fragment_count"] > 0


# ---------------------------------------------------------------------------
# Live: the end-to-end --check path
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def live_db_url() -> str:
    """The DSN for the real Regional Centre ingest, or a clean skip.

    Skips — never fails — when the corpus is not reachable, so the default
    suite runs offline. Where the corpus *is* reachable, the tests below are
    the automated gate ABS-464 asks for.
    """
    db_url = os.environ.get("BYLAW_REFERENCE_DB_URL", DEFAULT_DB_URL)
    sa = pytest.importorskip("sqlalchemy", reason="sqlalchemy not installed")
    try:
        engine = sa.create_engine(db_url, connect_args={"connect_timeout": 3})
        with engine.connect() as conn:
            document_id = conn.execute(
                sa.text("SELECT id FROM document WHERE bylaw_name = :name ORDER BY id LIMIT 1"),
                {"name": BYLAW_NAME},
            ).scalar()
    except sa.exc.SQLAlchemyError as exc:
        # Narrow on purpose. A bare `except Exception` would turn a typo in this
        # fixture into a permanent silent skip — the same class of always-green
        # guard ABS-464 exists to remove. Both real-world shapes are covered:
        # OperationalError (no Postgres listening) and ProgrammingError (a
        # database with no `document` table), each a SQLAlchemyError.
        pytest.skip(f"Regional Centre corpus not reachable at {db_url}: {type(exc).__name__}")

    if document_id is None:
        pytest.skip(
            f"no document row for bylaw_name={BYLAW_NAME!r} at {db_url} — "
            "this box has not run the real Regional Centre ingest"
        )
    return db_url


def test_check_passes_against_the_live_corpus(live_db_url, capsys):
    """DoD #4: the gate that would have caught the 4341-vs-4337 drift.

    If this fails after a re-ingest, that is the guard working: re-run
    `python scripts/build_bylaw_reference_index.py` and commit the diff.
    """
    exit_code = main(["--check", "--db-url", live_db_url])
    captured = capsys.readouterr()
    assert exit_code == 0, (
        "build_bylaw_reference_index.py --check failed against the live corpus:\n"
        f"{captured.err}{captured.out}"
    )
    assert "references resolve" in captured.out


def test_check_fails_when_the_committed_fragment_count_is_wrong(
    live_db_url, tmp_path, monkeypatch, capsys
):
    """DoD #5: prove the guard bites, rather than trusting that it would.

    Mutates `source_fragment_count` in a throwaway copy of the snapshot — the
    live corpus and the committed file are both left untouched — and asserts
    `--check` exits non-zero naming both numbers. Without this, DoD #1 could be
    satisfied by code that never executes.
    """
    committed = json.loads(INDEX_FILE.read_text())
    real_count = committed["source_fragment_count"]
    wrong_count = real_count + 4  # the size of the actual ABS-461 drift
    committed["source_fragment_count"] = wrong_count

    mutated = tmp_path / INDEX_FILE.name
    mutated.write_text(json.dumps(committed, indent=2, ensure_ascii=False) + "\n")
    monkeypatch.setattr(index_script, "INDEX_FILE", mutated)

    exit_code = main(["--check", "--db-url", live_db_url])
    captured = capsys.readouterr()

    assert exit_code == 1, "a snapshot describing the wrong corpus must not pass --check"
    assert str(wrong_count) in captured.err, "the stale count must be named"
    assert str(real_count) in captured.err, "the live count must be named"
    assert "snapshot is current" not in captured.out, (
        "DoD #2: a mode that failed verification must not claim the snapshot is current"
    )
    # The references all still resolve — this failure is purely about provenance,
    # and the message has to say so or the operator will hunt the wrong bug.
    assert "provenance" in captured.err


def test_check_does_not_rewrite_the_committed_snapshot(live_db_url):
    """`--check` is documented as "CI/preflight: no writes"."""
    before = INDEX_FILE.read_bytes()
    main(["--check", "--db-url", live_db_url])
    assert INDEX_FILE.read_bytes() == before
