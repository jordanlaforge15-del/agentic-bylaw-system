"""Regression coverage for the runtime-doc packaging workaround.

Three advisor modules read markdown files from disk at request time
using a ``Path(__file__).parents[3] / "docs" / ...`` resolver:

* ``advisor.chat.persona`` → ``docs/agent/persona.md``
* ``advisor.chat.persona`` (classifier persona) →
  ``docs/agent/classifier-persona.md``
* ``advisor.legal`` → ``docs/TERMS_AND_CONDITIONS.md``

Under an editable install the resolver lands at the repo root; under
the production non-editable install in ``Dockerfile.advisor`` it
lands at ``/opt/venv/lib/python3.11/docs/...`` instead. The
Dockerfile compensates with explicit ``COPY`` lines for each file.

ABS-67 happened because the COPY line for ``TERMS_AND_CONDITIONS.md``
was omitted when the T&C gate (ABS-18) shipped — every prod first-
login 500s with ``RuntimeError: Terms document not found ...``.
``make e2e`` does not catch this because the local Playwright stack
boots FastAPI from source, not from the built image.

This test enumerates the runtime-required docs and asserts each one
has a matching ``COPY`` line that lands it at the resolver's target
path. The next time someone adds a docs/*.md file backed by the same
workaround, this test fails until the Dockerfile is updated.
"""
from __future__ import annotations

from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# (source-relative-path-in-repo, container-destination-path).
# The destination must match ``Path(__file__).parents[3] / <relpath>``
# under the non-editable install in Dockerfile.advisor — i.e. rooted
# at ``/opt/venv/lib/python3.11``.
_RUNTIME_DOCS = [
    (
        "docs/agent/persona.md",
        "/opt/venv/lib/python3.11/docs/agent/persona.md",
    ),
    (
        "docs/agent/classifier-persona.md",
        "/opt/venv/lib/python3.11/docs/agent/classifier-persona.md",
    ),
    (
        "docs/TERMS_AND_CONDITIONS.md",
        "/opt/venv/lib/python3.11/docs/TERMS_AND_CONDITIONS.md",
    ),
]


@pytest.fixture(scope="module")
def dockerfile_text() -> str:
    return (_REPO_ROOT / "Dockerfile.advisor").read_text(encoding="utf-8")


@pytest.mark.parametrize("src,dest", _RUNTIME_DOCS)
def test_runtime_doc_is_copied_into_advisor_image(
    dockerfile_text: str, src: str, dest: str
) -> None:
    assert (_REPO_ROOT / src).is_file(), (
        f"Repo is missing {src}; the advisor cannot serve the feature "
        "that depends on it."
    )
    expected = f"COPY --chown=advisor:advisor {src} {dest}"
    assert expected in dockerfile_text, (
        f"Dockerfile.advisor is missing a COPY for {src}. Add:\n  "
        f"{expected}\nor the running container will 500 the moment "
        "the resolver tries to read this file."
    )
