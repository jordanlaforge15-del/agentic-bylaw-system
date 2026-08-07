"""Build-hygiene regression tests for Dockerfile.advisor (ABS-401).

This ticket bumped the advisor image's setuptools pin from ``<82`` to
``>=83`` and dropped the ``--ignore-vuln PYSEC-2026-3447`` exception from
the in-image ``pip-audit`` gate. Both facts are invisible to the Playwright
e2e suite and to the Python runtime tests: the setuptools pin only affects
the *build* of the advisor image, and e2e runs the FastAPI app from the
source venv, never from the built image. The honest coverage for a
build-time dependency pin is therefore a test that asserts the Dockerfile
invariants directly — the same pattern used for the e2e bash scripts in
``tests/test_e2e_port_recreate.py``.

These assertions fail loudly if a future edit reintroduces either the old
vulnerable pin or the audit exception, which is exactly the regression this
ticket exists to prevent.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = REPO_ROOT / "Dockerfile.advisor"


def _dockerfile_text() -> str:
    return DOCKERFILE.read_text(encoding="utf-8")


def _dockerfile_code() -> str:
    """Dockerfile text with ``#`` comments stripped, so assertions about the
    *active* build instructions aren't tripped by explanatory comments (which
    intentionally name the old pin and the closed CVE)."""
    return re.sub(r"#.*", "", _dockerfile_text())


def test_dockerfile_advisor_exists() -> None:
    assert DOCKERFILE.is_file(), f"expected {DOCKERFILE} to exist"


def test_setuptools_pinned_at_or_above_83() -> None:
    code = _dockerfile_code()
    # The install line must pin setuptools >=83 (the version that fixes
    # PYSEC-2026-3447). Match the quoted requirement passed to pip.
    assert re.search(r'"setuptools>=83"', code), (
        "Dockerfile.advisor must pin \"setuptools>=83\" — the fixed version "
        "for PYSEC-2026-3447 (ABS-401)"
    )
    # And it must NOT carry the old pre-fix upper bound as an active pin.
    assert "setuptools<82" not in code, (
        'the old "setuptools<82" pin carries PYSEC-2026-3447 and predates '
        "the pkg_resources audit done in ABS-401 — do not reintroduce it"
    )


def test_no_pysec_ignore_vuln_exception() -> None:
    code = _dockerfile_code()
    assert "--ignore-vuln" not in code, (
        "the pip-audit gate in Dockerfile.advisor must run WITHOUT an "
        "--ignore-vuln exception; PYSEC-2026-3447 is now fixed by the "
        "setuptools>=83 pin (ABS-401)"
    )
    assert "PYSEC-2026-3447" not in code, (
        "PYSEC-2026-3447 should only survive as an explanatory comment, not "
        "as an active audit-suppression argument"
    )


def test_pip_audit_still_runs_as_a_build_gate() -> None:
    code = _dockerfile_code()
    # Dropping the ignore flag only closes the exception if the audit still
    # runs — otherwise a reintroduced vuln would slip through unaudited.
    assert "pip-audit" in code, (
        "Dockerfile.advisor must still invoke pip-audit as a build-time "
        "vulnerability gate (ABS-401 removed the exception, not the audit)"
    )
