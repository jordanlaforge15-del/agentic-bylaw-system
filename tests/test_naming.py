"""ABS-434 — ``layer1.naming``: the shared bylaw-name normalizer.

One definition of "the same name modulo case/hyphen/whitespace drift",
shared by the enabled-name-collision audit, the ``enable-retrieval``
normalized-sibling warning, and the ABS-431 e2e fixture-name guard
(``scripts/e2e_fixture_names.py``). These tests pin the collapse rules and
prove the fixture guard actually delegates here — the two surfaces agreeing
is the point of the extraction.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from layer1.naming import normalize_bylaw_name, normalized_document_identity

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize(
    "variant",
    [
        "Regional Centre Land Use By-law",
        "Regional Centre Land Use By-Law",  # the doc-15/38 casing drift
        "regional centre land use by-law",
        "Regional Centre Land Use Bylaw",  # hyphen drift
        "Regional Centre Land Use By law",  # hyphen-to-space drift
        "Regional  Centre Land Use By-law",  # whitespace drift
        "REGIONAL CENTRE LAND USE BY-LAW",
        " Regional Centre Land Use By-law ",  # leading/trailing whitespace
        "Regional\tCentre Land Use By-law",  # tab
    ],
)
def test_all_drift_variants_normalize_identically(variant: str) -> None:
    assert normalize_bylaw_name(variant) == "regionalcentrelandusebylaw"


def test_genuinely_different_names_stay_distinct() -> None:
    assert normalize_bylaw_name("Halifax Peninsula Land Use By-law") != normalize_bylaw_name(
        "Halifax Mainland Land Use By-law"
    )


def test_casefold_not_just_lower() -> None:
    # casefold() collapses characters lower() misses (e.g. German sharp s).
    assert normalize_bylaw_name("Straße Bylaw") == normalize_bylaw_name("STRASSE BYLAW")


def test_normalized_document_identity_covers_both_halves() -> None:
    assert normalized_document_identity("HRM", "Test By-law") == normalized_document_identity(
        "hrm", "Test By-Law"
    )
    # Municipality drift alone also collides — the identity is the pair.
    assert normalized_document_identity("H R M", "Test Bylaw") == ("hrm", "testbylaw")
    assert normalized_document_identity("HRM", "Test Bylaw") != normalized_document_identity(
        "Halifax", "Test Bylaw"
    )


def test_fixture_guard_delegates_to_the_shared_normalizer() -> None:
    """The ABS-431 guard's ``_normalize`` must BE ``normalize_bylaw_name`` —
    identity, not merely equivalent behavior — so the guard and the ABS-434
    audit can never drift apart."""
    spec = importlib.util.spec_from_file_location(
        "e2e_fixture_names_for_naming_test", REPO_ROOT / "scripts" / "e2e_fixture_names.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module._normalize is normalize_bylaw_name
