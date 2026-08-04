"""ABS-431 guard: e2e fixture bylaw names must never collide with real bylaws.

The migration-0024 retrieval_enabled backfill and the ABS-355 relink pass
(``relink_superseded_datasets``) both match documents on ``(municipality,
bylaw_name)``. A fixture document seeded under a real bylaw's name therefore
competes with the real document for the enabled flag and can pull ``e2e_*``
geo datasets onto the real document (the ABS-429 contamination).

Enforcement is naming discipline with a single source of truth:
``scripts/e2e_fixture_names.py`` holds the real-bylaw list and the marker
predicate. This test AST-scans every ``scripts/seed_e2e_*.py`` for declared
``bylaw_name`` string constants — module-level ``*BYLAW_NAME*`` assignments,
``bylaw_name=...`` keyword arguments, and ``"bylaw_name": ...`` dict keys —
and asserts each one passes ``fixture_bylaw_name_violation``.

AST scanning (rather than importing the seed modules) keeps the test free of
the seeds' import side effects (``e2e_db_default`` mutates ``DATABASE_URL``)
and their layer1/advisor dependency graph.
"""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"

_spec = importlib.util.spec_from_file_location(
    "e2e_fixture_names", SCRIPTS / "e2e_fixture_names.py"
)
assert _spec is not None and _spec.loader is not None
e2e_fixture_names = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(e2e_fixture_names)

fixture_bylaw_name_violation = e2e_fixture_names.fixture_bylaw_name_violation
has_e2e_marker = e2e_fixture_names.has_e2e_marker
REAL_BYLAW_NAMES = e2e_fixture_names.REAL_BYLAW_NAMES


# ---------------------------------------------------------------------------
# Predicate behavior
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("real_name", REAL_BYLAW_NAMES)
def test_bare_real_name_is_rejected(real_name: str) -> None:
    assert fixture_bylaw_name_violation(real_name) is not None


@pytest.mark.parametrize(
    "variant",
    [
        "Regional Centre Land Use By-Law",  # casing variant
        "regional centre land use by-law",
        "Regional Centre Land Use Bylaw",  # hyphen variant
        "Regional  Centre Land Use By-law",  # whitespace variant
        "HALIFAX MAINLAND LAND USE BYLAW",
    ],
)
def test_normalized_real_name_variants_are_rejected(variant: str) -> None:
    assert fixture_bylaw_name_violation(variant) is not None


def test_real_name_with_e2e_marker_is_allowed() -> None:
    assert (
        fixture_bylaw_name_violation(
            "Regional Centre Land Use By-Law (Address Profile E2E)"
        )
        is None
    )


def test_real_name_reference_without_marker_is_rejected() -> None:
    violation = fixture_bylaw_name_violation(
        "Regional Centre Land Use By-Law (Address Profile)"
    )
    assert violation is not None
    assert "marker" in violation


def test_marker_alone_does_not_excuse_exact_equality() -> None:
    # Equality is what the backfill/relink passes match on; a name that IS a
    # real name must be rejected even if some variant carried a marker
    # elsewhere in the string-equality universe. (Exact-equal names cannot
    # contain the marker, so this is the equality branch's own message.)
    violation = fixture_bylaw_name_violation("Halifax Peninsula Land Use By-law")
    assert violation is not None
    assert "equals" in violation


@pytest.mark.parametrize(
    "name",
    [
        "Axis Binding Test By-law",
        "ABS-270 Structured Query E2E Bylaw",
        "Corpus Coherence Test Bylaw",
        "Table Citations Test By-law",
    ],
)
def test_unrelated_fixture_names_are_allowed(name: str) -> None:
    assert fixture_bylaw_name_violation(name) is None


def test_has_e2e_marker() -> None:
    assert has_e2e_marker("Foo (Bar E2E)")
    assert has_e2e_marker("e2e fixture")
    assert not has_e2e_marker("Regional Centre Land Use By-Law")


# ---------------------------------------------------------------------------
# Seed-script scan
# ---------------------------------------------------------------------------


def _declared_bylaw_names(path: Path) -> list[tuple[int, str]]:
    """Every string constant a seed script declares as a bylaw_name.

    Covers the three declaration shapes the seeds use:

    * ``DOCUMENT_BYLAW_NAME = "..."`` (any target containing BYLAW_NAME)
    * ``Document(..., bylaw_name="...")`` keyword arguments
    * ``{"bylaw_name": "..."}`` dict literals

    Aliased assignments (``NAME = OTHER_NAME``) resolve at the aliased
    constant's own declaration site, so skipping non-constants loses nothing.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[int, str]] = []

    def _is_str(node: ast.AST) -> bool:
        return isinstance(node, ast.Constant) and isinstance(node.value, str)

    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = [t.id for t in targets if isinstance(t, ast.Name)]
            if any("BYLAW_NAME" in n.upper() for n in names) and _is_str(node.value):
                found.append((node.lineno, node.value.value))  # type: ignore[union-attr]
        elif isinstance(node, ast.keyword):
            if node.arg == "bylaw_name" and _is_str(node.value):
                found.append((node.value.lineno, node.value.value))  # type: ignore[union-attr]
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if (
                    key is not None
                    and _is_str(key)
                    and key.value == "bylaw_name"  # type: ignore[union-attr]
                    and _is_str(value)
                ):
                    found.append((value.lineno, value.value))  # type: ignore[union-attr]
    return found


def _seed_scripts() -> list[Path]:
    return sorted(SCRIPTS.glob("seed_e2e_*.py"))


def test_seed_scripts_exist() -> None:
    assert len(_seed_scripts()) >= 15, "seed_e2e_*.py glob unexpectedly empty/small"


def test_scan_actually_finds_declarations() -> None:
    # Guard the guard: if a refactor moved every bylaw_name declaration into
    # a shape this scanner does not recognize, the collision test below would
    # pass vacuously. Require a healthy floor of scanned declarations.
    total = sum(len(_declared_bylaw_names(p)) for p in _seed_scripts())
    assert total >= 10, (
        f"only {total} bylaw_name declarations found across seed scripts — "
        f"the AST scan in _declared_bylaw_names no longer matches how seeds "
        f"declare names; update the scanner"
    )


@pytest.mark.parametrize("path", _seed_scripts(), ids=lambda p: p.name)
def test_no_seed_declares_a_real_bylaw_name(path: Path) -> None:
    problems = []
    for lineno, name in _declared_bylaw_names(path):
        violation = fixture_bylaw_name_violation(name)
        if violation is not None:
            problems.append(f"{path.name}:{lineno}: {violation}")
    assert not problems, (
        "e2e seed fixtures must never use a real bylaw name (ABS-431 — the "
        "(municipality, bylaw_name) backfill/relink match would bind them to "
        "the real document):\n" + "\n".join(problems)
    )
