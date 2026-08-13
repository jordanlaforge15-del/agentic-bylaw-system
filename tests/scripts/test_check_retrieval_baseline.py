"""Unit tests for scripts/check_retrieval_baseline.py (ABS-502).

The thing under test is a *gate*, so the two failure modes that matter are
asymmetric and both are fatal:

* A gate that misses real drift is the ABS-486 → ABS-492 failure repeating —
  a baseline that certifies a regression instead of catching it.
* A gate that fires on a reworded comment gets acknowledged reflexively within
  a week, and then it misses real drift too.

So these tests pin both directions: a behaviour-bearing edit must fail, and a
comment / docstring / reformatting edit must not. Everything runs against a
synthetic repo tree in ``tmp_path`` — no database, no corpus, no network —
except the last class, which runs the real check against this repo, which is
what makes `pytest` a merge gate rather than a description of one.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.check_retrieval_baseline import (
    ACKNOWLEDGED,
    ALGORITHM,
    FRESH,
    REGENERATE_COMMAND,
    STALE,
    CheckError,
    acknowledge,
    check,
    evaluate_freshness,
    main,
    normalise_json,
    normalise_python,
    retrieval_fingerprint,
    watched_files,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_BASELINE = REPO_ROOT / "evals" / "retrieval" / "BASELINE.json"


# ----------------------------------------------------------------------
# A synthetic repo carrying one file from every watched pattern
# ----------------------------------------------------------------------

SCORER = '''\
"""The scorer's docstring."""

CITATION_PATH_WEIGHT = 12


def score(fragment, request):
    # Own text is worth less than a path match.
    return CITATION_PATH_WEIGHT if fragment.path else 4
'''

QUERIES = {
    "provenance": {"tier": "agent-drafted, pending human spot-check", "review_status": "unreviewed"},
    "queries": [{"id": "RQ-D01", "category": "dimensional", "acceptable": []}],
}


@pytest.fixture()
def fake_repo(tmp_path: Path) -> Path:
    """A tree satisfying every WATCHED_PATTERNS entry.

    Every literal pattern must exist or ``watched_files`` refuses to run — that
    refusal is itself a tested behaviour (see ``test_missing_watched_path``).
    """
    files = {
        "mcp/bylaw_retrieval/retrieval/service.py": SCORER,
        "mcp/bylaw_retrieval/retrieval/context.py": "CONTEXT_WEIGHT = 2\n",
        "src/layer1/pipeline/hierarchy.py": "def ancestors(node):\n    return []\n",
        "src/layer1/pipeline/citation_repath.py": "def repath(x):\n    return x\n",
        "src/layer1/pipeline/corpus_repath.py": "def drive():\n    pass\n",
        "scripts/repath_citation_paths.py": "def main():\n    return 0\n",
        "scripts/eval_retrieval_recall.py": "K = 10\n",
        "evals/retrieval/queries.json": json.dumps(QUERIES, indent=2),
    }
    for relative, content in files.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return tmp_path


def write_baseline(repo: Path, *, recall: float = 0.5588) -> Path:
    """A baseline stamped against *repo* exactly as the harness would stamp it."""
    path = repo / "evals" / "retrieval" / "BASELINE.json"
    path.write_text(
        json.dumps(
            {
                "recall_at_k": recall,
                "mrr": 0.3077,
                "retrieval_fingerprint": retrieval_fingerprint(repo),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


# ----------------------------------------------------------------------
# Normalisation: what may and may not move the digest
# ----------------------------------------------------------------------


class TestNormalisation:
    def test_comments_are_not_significant(self) -> None:
        edited = SCORER.replace(
            "# Own text is worth less than a path match.",
            "# A path match outranks own text; see ABS-492 for why.",
        )
        assert edited != SCORER
        assert normalise_python(edited) == normalise_python(SCORER)

    def test_docstrings_are_not_significant(self) -> None:
        edited = SCORER.replace(
            '"""The scorer\'s docstring."""',
            '"""Rewritten at length, as this repo tends to do."""',
        )
        assert edited != SCORER
        assert normalise_python(edited) == normalise_python(SCORER)

    def test_blank_lines_and_trailing_space_are_not_significant(self) -> None:
        edited = SCORER.replace("CITATION_PATH_WEIGHT = 12", "CITATION_PATH_WEIGHT = 12   \n\n")
        assert normalise_python(edited) == normalise_python(SCORER)

    def test_a_changed_weight_is_significant(self) -> None:
        edited = SCORER.replace("CITATION_PATH_WEIGHT = 12", "CITATION_PATH_WEIGHT = 6")
        assert normalise_python(edited) != normalise_python(SCORER)

    def test_a_docstring_sharing_a_line_with_code_is_kept(self) -> None:
        """Dropping that line would take the signature with it and hide a rename."""
        one = 'def score(a): "doc"\n'
        two = 'def rank(a): "doc"\n'
        assert normalise_python(one) != normalise_python(two)

    def test_json_ignores_formatting_but_not_content(self) -> None:
        compact = json.dumps(QUERIES, separators=(",", ":"))
        assert normalise_json(compact, "queries") == normalise_json(
            json.dumps(QUERIES, indent=4), "queries"
        )

    def test_json_subset_excludes_the_provenance_header(self) -> None:
        # A human spot-check flips review_status; that changes no measurement.
        reviewed = json.loads(json.dumps(QUERIES))
        reviewed["provenance"]["review_status"] = "reviewed"
        assert normalise_json(json.dumps(reviewed), "queries") == normalise_json(
            json.dumps(QUERIES), "queries"
        )

    def test_json_subset_sees_a_changed_label(self) -> None:
        relabelled = json.loads(json.dumps(QUERIES))
        relabelled["queries"][0]["category"] = "permitted_use"
        assert normalise_json(json.dumps(relabelled), "queries") != normalise_json(
            json.dumps(QUERIES), "queries"
        )

    def test_unparseable_python_is_an_error_not_a_pass(self) -> None:
        with pytest.raises(CheckError):
            normalise_python("def f(\n")


# ----------------------------------------------------------------------
# The verdict
# ----------------------------------------------------------------------


class TestVerdict:
    def test_a_freshly_stamped_baseline_passes(self, fake_repo: Path) -> None:
        baseline = write_baseline(fake_repo)
        verdict = check(baseline, fake_repo)
        assert verdict.verdict == FRESH
        assert verdict.ok

    def test_a_changed_weight_makes_it_stale(self, fake_repo: Path) -> None:
        baseline = write_baseline(fake_repo)
        scorer = fake_repo / "mcp" / "bylaw_retrieval" / "retrieval" / "service.py"
        scorer.write_text(SCORER.replace("= 12", "= 6"), encoding="utf-8")

        verdict = check(baseline, fake_repo)
        assert verdict.verdict == STALE
        assert not verdict.ok
        assert verdict.modified == ("mcp/bylaw_retrieval/retrieval/service.py",)

    def test_a_reworded_comment_does_not(self, fake_repo: Path) -> None:
        """The eb613cf case: the commit that would have cried wolf."""
        baseline = write_baseline(fake_repo)
        scorer = fake_repo / "mcp" / "bylaw_retrieval" / "retrieval" / "service.py"
        scorer.write_text(
            SCORER.replace("# Own text is worth less", "# Reworded entirely: own text is worth less"),
            encoding="utf-8",
        )
        assert check(baseline, fake_repo).verdict == FRESH

    def test_a_relabelled_query_makes_it_stale(self, fake_repo: Path) -> None:
        baseline = write_baseline(fake_repo)
        relabelled = json.loads(json.dumps(QUERIES))
        relabelled["queries"][0]["acceptable"] = [{"bylaw": "x", "citation_path": "Part V > 135"}]
        (fake_repo / "evals" / "retrieval" / "queries.json").write_text(
            json.dumps(relabelled, indent=2), encoding="utf-8"
        )
        verdict = check(baseline, fake_repo)
        assert verdict.verdict == STALE
        assert verdict.modified == ("evals/retrieval/queries.json",)

    def test_a_new_retrieval_module_makes_it_stale(self, fake_repo: Path) -> None:
        """A channel added after the measurement is drift the glob has to see."""
        baseline = write_baseline(fake_repo)
        (fake_repo / "mcp" / "bylaw_retrieval" / "retrieval" / "spatial.py").write_text(
            "SPATIAL_WEIGHT = 9\n", encoding="utf-8"
        )
        verdict = check(baseline, fake_repo)
        assert verdict.verdict == STALE
        assert verdict.added == ("mcp/bylaw_retrieval/retrieval/spatial.py",)

    def test_a_deleted_retrieval_module_makes_it_stale(self, fake_repo: Path) -> None:
        baseline = write_baseline(fake_repo)
        (fake_repo / "mcp" / "bylaw_retrieval" / "retrieval" / "context.py").unlink()
        verdict = check(baseline, fake_repo)
        assert verdict.verdict == STALE
        assert verdict.removed == ("mcp/bylaw_retrieval/retrieval/context.py",)

    def test_a_baseline_with_no_fingerprint_is_stale(self, fake_repo: Path) -> None:
        """Every baseline written before ABS-502 — including the one on dev."""
        baseline = fake_repo / "evals" / "retrieval" / "BASELINE.json"
        baseline.write_text(json.dumps({"recall_at_k": 0.1029}), encoding="utf-8")
        verdict = check(baseline, fake_repo)
        assert verdict.verdict == STALE
        assert "no retrieval fingerprint" in verdict.reason

    def test_an_older_algorithm_cannot_be_compared(self, fake_repo: Path) -> None:
        baseline = write_baseline(fake_repo)
        document = json.loads(baseline.read_text())
        document["retrieval_fingerprint"]["algorithm"] = "significant-content-v0"
        baseline.write_text(json.dumps(document), encoding="utf-8")
        verdict = check(baseline, fake_repo)
        assert verdict.verdict == STALE
        assert "algorithm" in verdict.reason

    def test_a_missing_watched_path_is_an_error_not_a_pass(self, fake_repo: Path) -> None:
        """A watch list that has fallen out of date must not pass silently."""
        baseline = write_baseline(fake_repo)
        (fake_repo / "src" / "layer1" / "pipeline" / "hierarchy.py").unlink()
        with pytest.raises(CheckError, match="does not exist"):
            check(baseline, fake_repo)

    def test_an_unreadable_baseline_is_an_error(self, fake_repo: Path) -> None:
        with pytest.raises(CheckError, match="no baseline"):
            check(fake_repo / "nope.json", fake_repo)


# ----------------------------------------------------------------------
# The escape hatch
# ----------------------------------------------------------------------


class TestAcknowledgement:
    def test_acknowledging_drift_clears_the_verdict(self, fake_repo: Path) -> None:
        baseline = write_baseline(fake_repo)
        scorer = fake_repo / "mcp" / "bylaw_retrieval" / "retrieval" / "service.py"
        scorer.write_text(SCORER.replace("= 12", "= 6"), encoding="utf-8")
        assert check(baseline, fake_repo).verdict == STALE

        acknowledge(baseline, "dead code path, not reachable from search", fake_repo)
        verdict = check(baseline, fake_repo)
        assert verdict.verdict == ACKNOWLEDGED
        assert verdict.ok
        assert "dead code path" in verdict.reason

    def test_an_acknowledgement_covers_only_the_digest_it_was_granted_for(
        self, fake_repo: Path
    ) -> None:
        """The next edit fails the gate again — this is not a permanent waiver."""
        baseline = write_baseline(fake_repo)
        scorer = fake_repo / "mcp" / "bylaw_retrieval" / "retrieval" / "service.py"
        scorer.write_text(SCORER.replace("= 12", "= 6"), encoding="utf-8")
        acknowledge(baseline, "dead code path", fake_repo)

        scorer.write_text(SCORER.replace("= 12", "= 3"), encoding="utf-8")
        assert check(baseline, fake_repo).verdict == STALE

    def test_an_acknowledgement_needs_a_reason(self, fake_repo: Path) -> None:
        baseline = write_baseline(fake_repo)
        (fake_repo / "mcp" / "bylaw_retrieval" / "retrieval" / "service.py").write_text(
            SCORER.replace("= 12", "= 6"), encoding="utf-8"
        )
        with pytest.raises(CheckError, match="reason"):
            acknowledge(baseline, "   ", fake_repo)

    def test_a_blank_reason_in_the_file_does_not_clear_the_verdict(
        self, fake_repo: Path
    ) -> None:
        """Hand-editing the acknowledgement to an empty reason must not pass."""
        baseline = write_baseline(fake_repo)
        (fake_repo / "mcp" / "bylaw_retrieval" / "retrieval" / "service.py").write_text(
            SCORER.replace("= 12", "= 6"), encoding="utf-8"
        )
        acknowledge(baseline, "dead code path", fake_repo)
        document = json.loads(baseline.read_text())
        document["retrieval_fingerprint"]["acknowledged_drift"]["reason"] = ""
        baseline.write_text(json.dumps(document), encoding="utf-8")
        assert check(baseline, fake_repo).verdict == STALE

    def test_acknowledging_a_fresh_baseline_is_refused(self, fake_repo: Path) -> None:
        baseline = write_baseline(fake_repo)
        with pytest.raises(CheckError, match="already fresh"):
            acknowledge(baseline, "no reason to", fake_repo)

    def test_regeneration_drops_a_stale_acknowledgement(self, fake_repo: Path) -> None:
        """Re-measuring supersedes the waiver rather than carrying it forward."""
        baseline = write_baseline(fake_repo)
        scorer = fake_repo / "mcp" / "bylaw_retrieval" / "retrieval" / "service.py"
        scorer.write_text(SCORER.replace("= 12", "= 6"), encoding="utf-8")
        acknowledge(baseline, "dead code path", fake_repo)

        write_baseline(fake_repo)  # what `make eval-retrieval-baseline` writes
        document = json.loads(baseline.read_text())
        assert document["retrieval_fingerprint"]["acknowledged_drift"] is None
        assert check(baseline, fake_repo).verdict == FRESH


# ----------------------------------------------------------------------
# CLI contract
# ----------------------------------------------------------------------


class TestCli:
    def test_exit_codes_and_failure_message(self, fake_repo: Path, capsys) -> None:
        baseline = write_baseline(fake_repo)
        argv = ["--baseline", str(baseline), "--repo-root", str(fake_repo)]
        assert main(argv) == 0

        (fake_repo / "mcp" / "bylaw_retrieval" / "retrieval" / "service.py").write_text(
            SCORER.replace("= 12", "= 6"), encoding="utf-8"
        )
        assert main(argv) == 1
        stderr = capsys.readouterr().err
        # The whole point of the gate is that the failure says what to run.
        assert REGENERATE_COMMAND in stderr
        assert "mcp/bylaw_retrieval/retrieval/service.py" in stderr

    def test_a_misconfigured_check_exits_two(self, fake_repo: Path) -> None:
        baseline = write_baseline(fake_repo)
        (fake_repo / "src" / "layer1" / "pipeline" / "hierarchy.py").unlink()
        assert main(["--baseline", str(baseline), "--repo-root", str(fake_repo)]) == 2

    def test_json_output_is_machine_readable(self, fake_repo: Path, capsys) -> None:
        baseline = write_baseline(fake_repo)
        assert (
            main(["--baseline", str(baseline), "--repo-root", str(fake_repo), "--json"]) == 0
        )
        payload = json.loads(capsys.readouterr().out)
        assert payload["verdict"] == FRESH
        assert payload["regenerate_command"] == REGENERATE_COMMAND


# ----------------------------------------------------------------------
# The gate, against this repo
# ----------------------------------------------------------------------


class TestThisRepo:
    """These are the tests that make `pytest` the merge gate.

    A retrieval-affecting change that lands without re-recording the baseline
    fails here, in CI, on the PR — not months later in a post-mortem.
    """

    def test_the_watch_list_still_matches_the_tree(self) -> None:
        watched = watched_files(REPO_ROOT)
        assert "mcp/bylaw_retrieval/retrieval/service.py" in watched
        assert "src/layer1/pipeline/hierarchy.py" in watched
        assert "evals/retrieval/queries.json" in watched

    def test_the_committed_baseline_describes_the_committed_retrieval_code(self) -> None:
        verdict = check(REAL_BASELINE, REPO_ROOT)
        assert verdict.ok, (
            f"evals/retrieval/BASELINE.json is {verdict.verdict}: {verdict.reason}. "
            f"Run `{REGENERATE_COMMAND}` and commit the diff. Drifted: {verdict.drifted}"
        )

    def test_the_harness_stamps_the_block_the_checker_reads(self) -> None:
        recorded = json.loads(REAL_BASELINE.read_text())["retrieval_fingerprint"]
        assert recorded["algorithm"] == ALGORITHM
        assert recorded["digest"].startswith("sha256:")
        assert evaluate_freshness(
            {"retrieval_fingerprint": recorded}, retrieval_fingerprint(REPO_ROOT)
        ).ok
