"""Regression coverage for the runtime-doc packaging workaround.

Three advisor modules read markdown files from disk at request time
using a ``Path(__file__).parents[3] / "docs" / ...`` resolver:

* ``advisor.chat.persona`` → ``docs/agent/persona.md``
* ``advisor.chat.persona`` (classifier persona) →
  ``docs/agent/classifier-persona.md``
* ``advisor.legal`` → ``docs/TERMS_AND_CONDITIONS.md``

Under an editable install the resolver lands at the repo root; under
the production non-editable install in ``Dockerfile.advisor`` it
lands at ``/opt/venv/lib/python3.11/docs/...`` instead. Two things
have to be true for the file to actually arrive in the runtime image:

1. ``Dockerfile.advisor`` has a ``COPY --chown=advisor:advisor
   <src> <dest>`` line for it.
2. ``.dockerignore`` does *not* silently drop the source from the
   build context. ``docs/*`` excludes everything under ``docs/``, so
   each runtime doc needs an explicit ``!<src>`` re-include.

ABS-67 was caused by missing #1 (and we discovered missing #2 while
deploying the fix — the rebuild errored with "docs/TERMS_AND_CONDITIONS.md
not found" because ``docs/*`` had filtered it out before the COPY ran).
``make e2e`` cannot catch either failure because the local Playwright
stack boots FastAPI from source rather than from the built image.

This test enumerates the runtime-required docs and asserts both
invariants for each one. The next time someone adds a docs/*.md file
backed by the same workaround, this test fails until both files are
updated.
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


@pytest.fixture(scope="module")
def dockerignore_lines() -> list[str]:
    text = (_REPO_ROOT / ".dockerignore").read_text(encoding="utf-8")
    return [line.strip() for line in text.splitlines()]


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


@pytest.mark.parametrize("src,_dest", _RUNTIME_DOCS)
def test_runtime_doc_is_reincluded_in_dockerignore(
    dockerignore_lines: list[str], src: str, _dest: str
) -> None:
    """``.dockerignore`` must re-include each runtime doc explicitly.

    The ``docs/*`` exclude on line ~38 filters everything under
    ``docs/`` before buildx even sees the context. Without a matching
    ``!<src>`` line the COPY directive in Dockerfile.advisor errors
    with ``failed to compute cache key: ... not found`` at build time.
    A COPY line without a re-include is silently broken, so we assert
    both together.
    """
    expected = f"!{src}"
    assert expected in dockerignore_lines, (
        f".dockerignore does not re-include {src}. Add:\n  {expected}\n"
        "after the docs/* exclude, or `docker buildx build` will fail "
        "to find the file in the build context."
    )
