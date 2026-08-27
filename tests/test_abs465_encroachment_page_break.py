"""The second page break in the Regional Centre LUB (ABS-465).

ABS-461 fixed the pages 171/172 break in clause 198(1)(a) and covered it. The
same bylaw carries a second break, on pages 104/105 of subsection 94.5, which
production was found to be carrying too: the page-104 block ends mid-token at
"...abuts a lot containing an ER-" and page 105 opens "3, ER-2, ...", so the
old parser read the tail as a new section "3" and reparented the three
permitted-encroachment clauses under a phantom "Part V > 3".

The e2e spec asserts what the API can observe — that nothing resolves under
the phantom, and the clauses are reachable under the real 94.5. It cannot
assert the rejoin itself: the healed provision is unaddressed prose with no
citation_path, so /v1/citation has no way to ask for it. That guarantee lives
here, one layer down, where the fragment text is in hand.

The blocks come from the e2e seed rather than a copy, so the fixture the spec
runs against and the fixture asserted here cannot drift apart.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

from layer1.pipeline.hierarchy import reconstruct_hierarchy

REPO_ROOT = Path(__file__).resolve().parents[1]
SEED = REPO_ROOT / "scripts" / "seed_e2e_page_break_split.py"

PHANTOM = "Part V > 3"
REAL_SUBSECTION = "Part V > 94.5"
COMPLETE_ZONE_LIST = (
    "abuts a lot containing an ER-3, ER-2, ER-1, CH-2, CH-1, PCF, or RPK zone."
)


def _load_seed_module():
    """Import the seed for its block list only.

    The seed's first import is ``e2e_db_default``, which exists for its side
    effects: it sets DATABASE_URL process-wide and aborts the process unless
    the target database name ends in ``_test``. Neither belongs in a unit-test
    run, so it is stubbed out — nothing below touches a database.
    """
    sys.modules.setdefault("e2e_db_default", types.ModuleType("e2e_db_default"))
    spec = importlib.util.spec_from_file_location("abs465_seed", SEED)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def fragments():
    seed = _load_seed_module()
    blocks = seed._block_data(seed.ENCROACHMENT_BLOCKS, reading_order_base=980)
    return list(reconstruct_hierarchy(blocks))


def test_the_broken_token_is_rejoined(fragments):
    carriers = [f for f in fragments if COMPLETE_ZONE_LIST in f.text]
    assert carriers, (
        "no fragment carries the complete zone list; the page break was not "
        f"rejoined. Texts: {[f.text[-60:] for f in fragments]}"
    )


def test_no_fragment_is_left_truncated_at_the_break(fragments):
    # Pre-fix, the stored provision stopped at "an ER-" — a zone code cut in
    # half, which reads as a complete provision and is not one.
    truncated = [f.text for f in fragments if f.text.rstrip().endswith("ER-")]
    assert truncated == []


def test_the_tail_forges_no_section(fragments):
    # "3, ER-2, ..." opens with a bare number. That is the whole defect: it
    # looks exactly like the start of section 3.
    forged = [
        f.citation_path
        for f in fragments
        if f.citation_path
        and (f.citation_path == PHANTOM or f.citation_path.startswith(f"{PHANTOM} > "))
    ]
    assert forged == []


def test_the_encroachment_clauses_land_under_the_real_subsection(fragments):
    # The stepback distances a balcony question resolves to. Under the phantom
    # they were unreachable; they belong to 94.5.
    wanted = {
        "(a) 8.0 metres for a mid-rise building;",
        "(b) 12.5 metres for a tall mid-rise building; or",
        "(c) 12.5 metres for a high-rise building.",
    }
    homes = {
        f.text: f.citation_path for f in fragments if f.text in wanted
    }
    assert set(homes) == wanted, f"clauses missing from the parse: {wanted - set(homes)}"
    for text, path in homes.items():
        assert path and path.startswith(f"{REAL_SUBSECTION} > "), (
            f"{text!r} landed at {path!r}, not under {REAL_SUBSECTION}"
        )
