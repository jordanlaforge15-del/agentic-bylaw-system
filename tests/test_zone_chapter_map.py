"""ABS-471: the derived chapter map must keep describing the corpus.

``evals/regional_centre_zone_chapter_map.json`` is what makes "does this section
govern this case's zone?" answerable offline. It is derived from the ingest —
chapter headings and permitted-use table captions name their own zones — so the
failure mode is not "someone typed it wrong" but "the by-law was re-ingested and
the snapshot silently stopped matching". A stale boundary is worse than none: it
would clear a citation that has moved into another zone's chapter.

Two tiers, the split ABS-464 established:

* **Offline** — ``map_drift()`` is pure (two dicts in, strings out) and the
  committed snapshot's own internal consistency. No database; runs anywhere.
* **Live** — ``build_zone_chapter_map.py --check`` re-derives from the real
  4,300-fragment ingest. Skips cleanly where that corpus is absent, which is CI
  and every e2e worktree.
"""

from __future__ import annotations

import json
import os
from typing import Any

import pytest

import scripts.build_zone_chapter_map as map_script
from scripts.build_zone_chapter_map import (
    BYLAW_NAME,
    DEFAULT_DB_URL,
    MAP_FILE,
    load_map,
    main,
    map_drift,
    normalise_zone_token,
    zones_in,
)

# ---------------------------------------------------------------------------
# Offline: the tokeniser, the drift rule, and the committed snapshot
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "token,expected",
    [
        ("DD", "DD"),
        ("HR-1", "HR-1"),
        # Real PDF-extraction artefacts in the live corpus, not hypotheticals:
        # Part V Chapters 5/10/13 write "CEN- 2", Chapter 9 writes "ER3".
        ("CEN- 2", "CEN-2"),
        ("CH- 2", "CH-2"),
        ("ER3", "ER-3"),
        ("HCD-SV", "HCD-SV"),
        ("and", None),
        ("Waterfront Special Areas", None),
        ("Schmidtville Heritage Conservation District (SHCD)", None),
    ],
)
def test_zone_tokens_survive_the_ingest_s_typography(token, expected):
    assert normalise_zone_token(token) == expected


def test_a_chapter_heading_s_zone_list_is_read_in_full():
    """Chapter 9 governs three zones; reading one of them would clear the rest."""
    assert zones_in("ER3, ER-2, and ER-1") == ["ER-3", "ER-2", "ER-1"]
    # Part VI Chapter 2 separates the district's name from the zone with a slash.
    assert zones_in("Schmidtville Heritage Conservation District (SHCD) / HCD-SV") == [
        "HCD-SV"
    ]


def _snapshot(**overrides: Any) -> dict[str, Any]:
    base = {
        "bylaw_name": BYLAW_NAME,
        "document_id": 4,
        "source_fragment_count": 4337,
        "chapters": [
            {
                "part": "V",
                "chapter": 7,
                "title": "Built Form and Siting Requirements within the HR-2 and HR-1 Zones",
                "first_section": 195,
                "last_section": 211,
                "zones": ["HR-2", "HR-1"],
                "sections": list(range(195, 212)),
            }
        ],
        "permitted_use_tables": {"Table 1A": ["DD", "HR-1"]},
    }
    base.update(overrides)
    return base


def test_no_drift_when_the_snapshot_matches_the_corpus():
    assert map_drift(_snapshot(), _snapshot()) == []


def test_drift_names_a_moved_chapter_boundary_with_both_numbers():
    """A boundary that moves silently is the whole reason this file exists."""
    stale = _snapshot()
    stale["chapters"][0]["last_section"] = 209
    drift = map_drift(stale, _snapshot())
    assert any("Part V Chapter 7" in line and "209" in line and "211" in line for line in drift)


def test_drift_catches_a_chapter_that_changed_which_zones_it_governs():
    stale = _snapshot()
    stale["chapters"][0]["zones"] = ["HR-2"]
    drift = map_drift(stale, _snapshot())
    assert any("zones" in line for line in drift), drift


def test_drift_catches_a_chapter_the_corpus_no_longer_has():
    live = _snapshot(chapters=[])
    drift = map_drift(_snapshot(), live)
    assert drift == ["Part V Chapter 7 is in the snapshot but not the corpus"]


def test_drift_catches_a_retabled_permitted_use_table():
    stale = _snapshot(permitted_use_tables={"Table 1A": ["DD"]})
    drift = map_drift(stale, _snapshot())
    assert any("Table 1A" in line for line in drift)


def test_drift_catches_a_re_ingest_that_changed_the_fragment_count():
    drift = map_drift(_snapshot(source_fragment_count=4341), _snapshot())
    assert len(drift) == 1
    assert "4341" in drift[0] and "4337" in drift[0]


def test_the_committed_snapshot_covers_the_zones_the_eval_uses():
    """Every zone-specific chapter and every permitted-use table, present.

    Cheap and database-free, and it catches a hand-edited or half-written
    snapshot — the failure that would make every G2/G3 assertion vacuous.
    """
    committed = load_map()
    assert committed["bylaw_name"] == BYLAW_NAME
    zoned = [c for c in committed["chapters"] if c["zones"]]
    assert len(zoned) >= 18, "the by-law has 18+ zone-specific chapters"
    assert set(committed["permitted_use_tables"]) == {
        "Table 1A",
        "Table 1B",
        "Table 1C",
        "Table 1D",
    }
    # The ranges ABS-471 names explicitly, as a floor under the derivation.
    by_key = {(c["part"], c["chapter"]): c for c in committed["chapters"]}
    assert by_key[("V", 2)]["first_section"] == 107 and by_key[("V", 2)]["last_section"] == 128
    assert by_key[("V", 7)]["zones"] == ["HR-2", "HR-1"]
    assert by_key[("VI", 2)]["zones"] == ["HCD-SV"]


def test_every_chapter_carries_the_sections_its_range_advertises():
    for chapter in load_map()["chapters"]:
        sections = chapter["sections"]
        assert sections, f"Part {chapter['part']} Chapter {chapter['chapter']} has no sections"
        assert sections == sorted(set(sections))
        assert chapter["first_section"] == sections[0]
        assert chapter["last_section"] == sections[-1]


def test_no_zone_is_governed_by_two_built_form_chapters():
    """Two chapters claiming one zone would make the rule ambiguous.

    Part V's built-form chapters partition the zones. (Signage — Part XIV —
    legitimately re-covers them, so the check is scoped to Part V.)
    """
    seen: dict[str, str] = {}
    for chapter in load_map()["chapters"]:
        if chapter["part"] != "V":
            continue
        for zone in chapter["zones"]:
            key = f"Chapter {chapter['chapter']}"
            assert zone not in seen, f"{zone} is governed by both {seen[zone]} and {key}"
            seen[zone] = key


# ---------------------------------------------------------------------------
# Live: re-derive from the real ingest
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def live_db_url() -> str:
    """The DSN for the real Regional Centre ingest, or a clean skip."""
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
        # Narrow on purpose: a bare `except Exception` would turn a typo in this
        # fixture into a permanent silent skip.
        pytest.skip(f"Regional Centre corpus not reachable at {db_url}: {type(exc).__name__}")
    if document_id is None:
        pytest.skip(f"no document row for bylaw_name={BYLAW_NAME!r} at {db_url}")
    return db_url


def test_check_passes_against_the_live_corpus(live_db_url, capsys):
    exit_code = main(["--check", "--db-url", live_db_url])
    captured = capsys.readouterr()
    assert exit_code == 0, (
        "build_zone_chapter_map.py --check failed against the live corpus:\n"
        f"{captured.err}{captured.out}"
    )
    assert "chapters" in captured.out


def test_check_fails_when_a_committed_boundary_has_moved(
    live_db_url, tmp_path, monkeypatch, capsys
):
    """Prove the guard bites rather than trusting that it would.

    Mutates a chapter's last section in a throwaway copy — the live corpus and
    the committed file are both left untouched — and asserts --check exits
    non-zero naming the chapter and both numbers.
    """
    committed = json.loads(MAP_FILE.read_text())
    target = next(c for c in committed["chapters"] if c["zones"])
    real_last = target["last_section"]
    target["last_section"] = real_last + 5

    mutated = tmp_path / MAP_FILE.name
    mutated.write_text(json.dumps(committed, indent=2, ensure_ascii=False) + "\n")
    monkeypatch.setattr(map_script, "MAP_FILE", mutated)

    exit_code = main(["--check", "--db-url", live_db_url])
    captured = capsys.readouterr()

    assert exit_code == 1, "a snapshot whose boundaries have moved must not pass --check"
    assert f"Part {target['part']} Chapter {target['chapter']}" in captured.err
    assert str(real_last + 5) in captured.err and str(real_last) in captured.err


def test_check_does_not_rewrite_the_committed_snapshot(live_db_url):
    before = MAP_FILE.read_bytes()
    main(["--check", "--db-url", live_db_url])
    assert MAP_FILE.read_bytes() == before
