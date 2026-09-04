"""ABS-533 — every surface that names a Python version must name the same one.

WHAT THIS IS FOR. The interpreter is declared in eight places that no tool
reconciles: the advisor image's two ``FROM`` lines, the layer image's ``FROM``,
four CI jobs, the dependency audit's job, the version the locks are compiled
against, ``requires-python``, and ruff's ``target-version``. Before this ticket
they agreed only because nobody had touched any of them since 2024 — the image
was pinned to ``python:3.11.10-slim-bookworm``, released September 2024, and CI
and the dev venv had drifted to whatever the box had.

A *partial* move is worse than no move. Bump the image and leave CI on the old
minor and the tests no longer run on the interpreter that ships — which is the
exact shape of ABS-531, where the suite ran green against an anthropic the image
did not have. ABS-532 closed that for package versions with a lockfile; nothing
closed it for the interpreter, and a lock is compiled *against* an interpreter,
so the two can disagree while both look pinned.

WHY A TEST AND NOT A DOC. The move is rare and the surfaces are spread across
Dockerfiles, YAML and shell, so no reviewer holds all eight in their head. This
test is what makes the next bump mechanical: change ``EXPECTED`` here, run the
suite, and it names every file still on the old version.

The exact patch (3.13.15) lives only in Dockerfile.advisor. CI's
``setup-python`` and ``requires-python`` are deliberately minor-only —
pinning a patch there would fail the day GitHub's runner image rebuilt, for no
safety gained, since the artifact that reaches production is the image.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]

# The deploy target. Changing this is the whole of a runtime move; everything
# below is derived from it, and every assertion is a place that has to follow.
#
# 3.13 and not 3.14 (ABS-533): both install and import the full [advisor] set
# from binary wheels in a real linux/amd64 container — ifcopenshell 0.8.5,
# pyproj 3.7.2, shapely 2.1.2 and psycopg[binary] 3.3.5 all publish manylinux
# wheels for cp313 and cp314. The unlocked [parsers] toolchain is what decided
# it: paddleocr/paddlex constrain pyyaml to 6.0.2, which publishes no cp314
# manylinux wheel, so a 3.14 dev box building `--with-parsers` compiles it from
# source. 3.11 was the status quo and is in security-fix-only maintenance.
EXPECTED_MINOR = "3.13"
EXPECTED_IMAGE_PATCH = "3.13.15"


def _read(relpath: str) -> str:
    return (REPO_ROOT / relpath).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Container images — the artifact that actually reaches production
# ---------------------------------------------------------------------------


def test_advisor_image_pins_the_exact_patch_release() -> None:
    """Both stages of Dockerfile.advisor, and both on the same exact patch.

    Exact, not ``python:3.13-slim-bookworm``: an unpinned tag is a moving base
    image, and the one part of this build that never drifted while ``anthropic``
    did (ABS-531) was the part that carried a patch-level pin. The two stages
    must match each other too — the runtime stage copies ``/opt/venv`` wholesale
    from the builder, so a builder on a different minor produces a venv whose
    ``site-packages`` the runtime interpreter does not even look in.
    """
    froms = re.findall(r"^FROM (python:\S+)", _read("Dockerfile.advisor"), re.MULTILINE)

    assert froms == [
        f"python:{EXPECTED_IMAGE_PATCH}-slim-bookworm",
        f"python:{EXPECTED_IMAGE_PATCH}-slim-bookworm",
    ], f"Dockerfile.advisor base images are {froms}"


def test_layer_image_tracks_the_same_minor() -> None:
    """Dockerfile.layer is the ingest toolbox, not a deploy target.

    It gets the minor tag rather than the patch: it ships nothing, and holding a
    patch pin on an image nobody deploys is maintenance with no reader. What it
    must not do is sit on a *different minor* than the advisor, because it is
    where ingest is exercised before its output is served by the advisor.
    """
    froms = re.findall(r"^FROM (python:\S+)", _read("Dockerfile.layer"), re.MULTILINE)

    assert froms == [f"python:{EXPECTED_MINOR}-slim-bookworm"], (
        f"Dockerfile.layer base image is {froms}"
    )


# ---------------------------------------------------------------------------
# CI — where the tests that gate the merge actually run
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "workflow", [".github/workflows/ci.yml", ".github/workflows/dependency-audit.yml"]
)
def test_every_ci_job_runs_the_deploy_target(workflow: str) -> None:
    """Every ``setup-python`` in every job, not just the ones we remember.

    Walked out of the parsed YAML rather than grepped, so a job added later is
    covered without anyone updating a list here. A job left behind on an old
    minor is a suite whose green does not describe the artifact — the failure
    mode this whole ticket exists to remove.
    """
    document = yaml.safe_load(_read(workflow))

    versions = {
        (job_name, step.get("with", {}).get("python-version"))
        for job_name, job in document["jobs"].items()
        for step in job.get("steps", [])
        if str(step.get("uses", "")).startswith("actions/setup-python")
    }
    assert versions, f"{workflow} has no setup-python step; did the workflow move?"

    off_target = sorted(f"{job}={version!r}" for job, version in versions if version != EXPECTED_MINOR)
    assert not off_target, (
        f"{workflow} jobs are not on Python {EXPECTED_MINOR}: {off_target}"
    )


# ---------------------------------------------------------------------------
# Package metadata and tooling
# ---------------------------------------------------------------------------


def test_requires_python_floor_is_the_deploy_target() -> None:
    """``requires-python`` is the floor uv resolves the universal lock from.

    Leaving it below the deploy target does not just permit an old interpreter:
    it makes the locks fork requirements across versions nothing runs (the
    committed locks carried two ``numpy`` pins, one for ``< 3.12``, purely
    because the floor said 3.11), and it lets a contributor build a venv the
    image could not.
    """
    pyproject = tomllib.loads(_read("pyproject.toml"))

    assert pyproject["project"]["requires-python"] == f">={EXPECTED_MINOR}"


def test_ruff_target_version_matches() -> None:
    """A lint target below the runtime silently forbids syntax that would run.

    Harmless-looking and one-directional: ruff on ``py311`` flags modern syntax
    the 3.13 image executes fine, so the cost lands on whoever writes it and
    reads as a style rule rather than a stale pin.
    """
    pyproject = tomllib.loads(_read("pyproject.toml"))

    expected = "py" + EXPECTED_MINOR.replace(".", "")
    assert pyproject["tool"]["ruff"]["target-version"] == expected


def test_lock_script_compiles_against_the_deploy_target() -> None:
    """The locks are compiled ``--python-version``; that has to be the runtime.

    ABS-532's lock is universal, so it *installs* on newer interpreters — which
    is what makes this easy to leave stale and hard to notice. But the floor
    decides which fork of a marker-split requirement is chosen and which wheels
    are hashed into the file, so a lock compiled for 3.11 and installed on 3.13
    is a lock describing an environment nothing runs.
    """
    script = _read("scripts/lock-python-deps.sh")

    assert re.search(rf'^LOCK_PYTHON_VERSION="{re.escape(EXPECTED_MINOR)}"$', script, re.MULTILINE), (
        "scripts/lock-python-deps.sh does not compile the locks against "
        f"Python {EXPECTED_MINOR}"
    )


@pytest.mark.parametrize(
    "script", ["scripts/dev-setup.sh", "scripts/setup-layer-env.sh"]
)
def test_setup_scripts_refuse_an_interpreter_older_than_the_target(script: str) -> None:
    """The dev venv is the other half of the split ABS-531 fell through.

    Both provisioning scripts gate on ``sys.version_info``; that gate is what
    stops a developer running the suite on an interpreter the image does not
    ship and reporting a green that describes their laptop.
    """
    major, minor = EXPECTED_MINOR.split(".")

    assert f"sys.version_info < ({major}, {minor})" in _read(script), (
        f"{script} does not require Python {EXPECTED_MINOR}+"
    )
