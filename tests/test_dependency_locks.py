"""ABS-532 — the committed Python dependency locks, and the sites that install them.

These tests are the in-process half of the lock guarantee. The other half is
CI's ``lock-drift`` job, which recompiles ``requirements/*.txt`` from
``pyproject.toml`` and diffs; that needs network and the pinned resolver, so it
cannot live here.

What *can* live here is everything about the locks that is checkable from the
files on disk, and that is most of what actually went wrong:

*   Every lock entry is exactly pinned and carries hashes. ``--require-hashes``
    enforces this at install time, but only on the machine doing the install —
    a lock that silently lost its hashes would fail the deploy rather than the
    test suite, which is the wrong end to find out.
*   A package in more than one lock carries the same version everywhere. Three
    files are resolved by three ``uv pip compile`` invocations; nothing in uv
    makes them agree. ``scripts/lock-python-deps.sh`` runs all three together so
    they do, and this is what notices if that ever stops being true.
*   Each of the five historical install sites installs from a lock, with
    ``--require-hashes``, and does not re-resolve ``pyproject.toml`` afterwards.
    This is the regression test for the ticket itself: the failure mode is not
    "the lock is wrong", it is "somebody added a sixth install site", or
    "somebody dropped ``--no-deps`` and pip quietly resolved past the pins".
*   Every direct dependency declared in ``pyproject.toml`` appears in the lock
    that is supposed to cover it. A lock compiled against a stale pyproject is
    a lock that installs the wrong software.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS_DIR = REPO_ROOT / "requirements"

# name -> the pyproject extras it is compiled with. Mirrors LOCK_SPECS in
# scripts/lock-python-deps.sh; the two are asserted to agree below, so this is a
# real cross-check rather than a copy that can rot.
EXPECTED_LOCKS: dict[str, tuple[str, ...]] = {
    "base.txt": (),
    "runtime.txt": ("advisor",),
    "dev.txt": ("dev", "advisor"),
}

# A requirement line: "name==version" optionally followed by "; markers" and a
# line continuation. uv emits names already normalised to lowercase-with-dashes.
_PINNED = re.compile(r"^(?P<name>[A-Za-z0-9._-]+)==(?P<version>[^\s;\\]+)")


def _normalise(name: str) -> str:
    """PEP 503 name normalisation — ``PyYAML`` and ``pyyaml`` are one package."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _read_lock(path: Path) -> dict[str, set[str]]:
    """Map normalised package name -> the set of versions the lock pins it to.

    Usually one version per name. It is legitimately more than one when
    universal resolution had to fork a requirement across Python versions or
    platforms — ``numpy`` is pinned twice, once under
    ``python_full_version < '3.12'`` and once under ``>= '3.12'`` — which is
    exactly the mechanism that lets a single file serve linux/3.11 and
    macOS/3.12. Hence a set, not a scalar.
    """
    pins: dict[str, set[str]] = {}
    for line in path.read_text().splitlines():
        match = _PINNED.match(line)
        if match:
            pins.setdefault(_normalise(match["name"]), set()).add(match["version"])
    return pins


@pytest.fixture(scope="module")
def pyproject() -> dict:
    return tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())


@pytest.fixture(scope="module")
def locks() -> dict[str, dict[str, set[str]]]:
    return {name: _read_lock(REQUIREMENTS_DIR / name) for name in EXPECTED_LOCKS}


# ---------------------------------------------------------------------------
# The lock files themselves
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("lock_name", sorted(EXPECTED_LOCKS))
def test_lock_file_exists_and_is_not_empty(lock_name: str) -> None:
    path = REQUIREMENTS_DIR / lock_name
    assert path.is_file(), (
        f"requirements/{lock_name} is missing. Generate the locks with "
        f"./scripts/lock-python-deps.sh — they are committed, not built on demand."
    )
    assert _read_lock(path), f"requirements/{lock_name} contains no pinned requirements"


@pytest.mark.parametrize("lock_name", sorted(EXPECTED_LOCKS))
def test_every_requirement_is_pinned_and_hashed(lock_name: str) -> None:
    """No floors, no bare names, and a hash on every entry.

    ``>=`` in a lock file is the bug this ticket is about, written down. A
    missing hash is subtler: pip's ``--require-hashes`` rejects the whole file
    the moment one entry lacks a hash, so the install site fails rather than
    quietly installing something unverified — but it fails on the deploy, not
    here.
    """
    text = (REQUIREMENTS_DIR / lock_name).read_text()
    lines = text.splitlines()

    unpinned: list[str] = []
    unhashed: list[str] = []
    for index, line in enumerate(lines):
        if not line or line.startswith((" ", "\t", "#")):
            continue
        if not _PINNED.match(line):
            unpinned.append(line)
            continue
        # Hashes are emitted as indented `--hash=` continuations directly
        # beneath the requirement.
        name = _PINNED.match(line)["name"]  # type: ignore[index]
        following = []
        for candidate in lines[index + 1 :]:
            if not candidate.startswith((" ", "\t")):
                break
            following.append(candidate.strip())
        if not any(item.startswith("--hash=") for item in following):
            unhashed.append(name)

    assert not unpinned, (
        f"requirements/{lock_name} has requirements that are not exact pins: {unpinned}. "
        f"A floor in a lock file defeats the lock."
    )
    assert not unhashed, (
        f"requirements/{lock_name} has requirements with no --hash: {unhashed}. "
        f"pip --require-hashes rejects the entire file for one missing hash, so this "
        f"breaks every install site at once."
    )


def test_locks_agree_on_every_shared_package(locks: dict[str, dict[str, set[str]]]) -> None:
    """base/runtime/dev must not disagree about a version.

    Three separate ``uv pip compile`` runs produce these; nothing in the
    resolver ties them together. ``scripts/lock-python-deps.sh`` regenerates all
    three in one invocation so they land on the same answer, but a partial
    regeneration — or a hand-edit — would split them, and a split means the
    image and the test venv are once again running different software while
    both claim to be locked.
    """
    every_name = set().union(*(set(pins) for pins in locks.values()))

    disagreements: dict[str, dict[str, set[str]]] = {}
    for name in sorted(every_name):
        by_lock = {lock: pins[name] for lock, pins in locks.items() if name in pins}
        if len({frozenset(versions) for versions in by_lock.values()}) > 1:
            disagreements[name] = by_lock

    assert not disagreements, (
        f"the locks pin different versions of the same package: {disagreements}. "
        f"Regenerate all three together with ./scripts/lock-python-deps.sh."
    )


def test_lock_scope_is_nested(locks: dict[str, dict[str, set[str]]]) -> None:
    """base ⊆ runtime ⊆ dev, because the extras are.

    ``dev.txt`` is ``[dev,advisor]``, ``runtime.txt`` is ``[advisor]``,
    ``base.txt`` is neither, so each is a superset of the one before. If that
    inverts, a lock was compiled with the wrong extras — which would mean, for
    instance, the golden gate installing FastAPI, or the shipped image missing a
    runtime dependency it needs.
    """
    assert set(locks["base.txt"]) <= set(locks["runtime.txt"]), (
        "requirements/base.txt is not a subset of runtime.txt; the extras used to "
        "compile them are wrong"
    )
    assert set(locks["runtime.txt"]) <= set(locks["dev.txt"]), (
        "requirements/runtime.txt is not a subset of dev.txt; the extras used to "
        "compile them are wrong"
    )


def test_dev_only_tooling_stays_out_of_the_shipped_lock(
    locks: dict[str, dict[str, set[str]]],
) -> None:
    """The image must not ship the test toolchain.

    Not a size argument — a linter and a test runner in a production image are
    extra attack surface and extra CVEs to triage on an audit that has nothing
    to do with what the service does.
    """
    leaked = sorted(
        name
        for name in ("pytest", "pytest-cov", "pytest-asyncio", "ruff", "pip-audit")
        if name in locks["runtime.txt"]
    )
    assert not leaked, (
        f"{leaked} are [dev] tools but appear in requirements/runtime.txt, which is "
        f"what Dockerfile.advisor installs. Check the extras in LOCK_SPECS."
    )


@pytest.mark.parametrize(
    ("lock_name", "extras"),
    sorted((name, extras) for name, extras in EXPECTED_LOCKS.items()),
)
def test_declared_dependencies_are_present_in_their_lock(
    lock_name: str, extras: tuple[str, ...], pyproject: dict, locks: dict
) -> None:
    """Every direct dependency in pyproject.toml is pinned in the lock covering it.

    This is what catches a lock compiled before the last ``pyproject.toml``
    edit. CI's drift job catches it too and more thoroughly, but it needs
    network and the pinned resolver; this runs in the unit suite, offline, on
    every commit.
    """
    declared: list[str] = list(pyproject["project"]["dependencies"])
    for extra in extras:
        declared.extend(pyproject["project"]["optional-dependencies"][extra])

    pins = locks[lock_name]
    missing = []
    for requirement in declared:
        # "psycopg[binary]>=3.1" -> "psycopg"; "anthropic==0.100.0" -> "anthropic"
        name = re.split(r"[<>=!~\[;\s]", requirement, maxsplit=1)[0]
        if _normalise(name) not in pins:
            missing.append(name)

    assert not missing, (
        f"{missing} are declared in pyproject.toml for extras {extras or '(none)'} but "
        f"absent from requirements/{lock_name}. Regenerate with "
        f"./scripts/lock-python-deps.sh."
    )


def test_pinned_anthropic_pin_survives_into_the_locks(locks: dict) -> None:
    """The ABS-531 pin is not just a declaration; it has to reach the install.

    ``anthropic==0.100.0`` in pyproject.toml only constrains a resolution that
    actually reads pyproject.toml. Now that every site installs from a lock, the
    pin matters only insofar as the lock carries it.
    """
    for lock_name in ("runtime.txt", "dev.txt"):
        assert locks[lock_name].get("anthropic") == {"0.100.0"}, (
            f"requirements/{lock_name} does not pin anthropic to 0.100.0 — the version "
            f"the suite is actually run against (ABS-531). Moving off it is the 1.x "
            f"migration's job, done deliberately with tests."
        )


# ---------------------------------------------------------------------------
# The install sites
# ---------------------------------------------------------------------------

# The five sites the ticket enumerated, plus the Makefile convenience target,
# and which lock each must install. Adding a site without adding it here is
# fine; adding one that resolves pyproject.toml directly is what this guards.
INSTALL_SITES: tuple[tuple[str, str], ...] = (
    ("Dockerfile.advisor", "requirements/runtime.txt"),
    ("scripts/dev-setup.sh", "requirements/dev.txt"),
    (".github/workflows/dependency-audit.yml", "requirements/dev.txt"),
    ("Makefile", "requirements/dev.txt"),
)


@pytest.mark.parametrize(("relative_path", "lock"), INSTALL_SITES)
def test_install_site_reads_the_lock(relative_path: str, lock: str) -> None:
    text = (REPO_ROOT / relative_path).read_text()
    assert lock in text, (
        f"{relative_path} does not reference {lock}. Every install site must install "
        f"from a committed lock — five independently-resolving sites is what ABS-532 "
        f"removed."
    )


def test_ci_installs_both_locks_it_needs() -> None:
    """ci.yml has two install sites with deliberately different scopes."""
    text = (REPO_ROOT / ".github/workflows/ci.yml").read_text()
    assert "requirements/dev.txt" in text, "ci.yml's Python tests job must install the dev lock"
    assert "requirements/base.txt" in text, (
        "ci.yml's golden-gate job must install requirements/base.txt — the no-extras "
        "lock. Any wider lock hands the gate FastAPI and deletes the constraint that "
        "keeps its imports honest."
    )


@pytest.mark.parametrize(
    "relative_path",
    [
        "Dockerfile.advisor",
        "scripts/dev-setup.sh",
        ".github/workflows/ci.yml",
        "Makefile",
    ],
)
def test_install_sites_require_hashes(relative_path: str) -> None:
    """``pip install -r <lock>`` without ``--require-hashes`` is not a locked install.

    pip will happily install a fully-pinned file without the flag — and will
    also happily install anything a resolver decides to add alongside it. The
    flag is what makes an unrecorded package an error.
    """
    text = (REPO_ROOT / relative_path).read_text()
    # `pip install ... -r <lock>` only. `pip-audit -r <lock>` also reads a lock
    # but installs nothing, so --require-hashes is meaningless there.
    for match in re.finditer(r"pip install[^\n]*-r [^\n]*requirements/\w+\.txt", text):
        assert "--require-hashes" in match.group(0), (
            f"{relative_path} installs a lock without --require-hashes:\n"
            f"    {match.group(0).strip()}"
        )


@pytest.mark.parametrize(
    "relative_path",
    [
        "Dockerfile.advisor",
        "scripts/dev-setup.sh",
        ".github/workflows/ci.yml",
        "Makefile",
    ],
)
def test_project_is_installed_without_re_resolving(relative_path: str) -> None:
    """``pip install .`` after a locked install undoes the lock.

    pip re-reads pyproject.toml, sees the floors, and is free to upgrade past
    every pin that was just honoured — silently, because the pins came from a
    different invocation. ``--no-deps`` is what stops it. This is the single
    easiest way to reintroduce the bug while leaving the lock files looking
    perfectly correct.
    """
    text = (REPO_ROOT / relative_path).read_text()
    offenders = [
        line.strip()
        for line in text.splitlines()
        # Match a project install: `pip install .` or `pip install -e .`,
        # allowing an extras suffix. Not `-r`, which is the lock install.
        if re.search(r"pip install (?:-e )?\"?\.(?:\[[^\]]*\])?\"?(?:\s|$)", line)
        and "--no-deps" not in line
    ]
    assert not offenders, (
        f"{relative_path} installs the project without --no-deps:\n"
        + "\n".join(f"    {line}" for line in offenders)
        + "\nThat lets pip re-resolve pyproject.toml's floors and walk straight past "
        "the versions the lock just pinned."
    )


def test_lock_script_specs_match_this_modules_expectations() -> None:
    """The script's LOCK_SPECS and EXPECTED_LOCKS above must not drift apart.

    Without this the test module is a second, unverified copy of the script's
    configuration, and a lock silently compiled with different extras would pass
    every assertion here.
    """
    script = (REPO_ROOT / "scripts/lock-python-deps.sh").read_text()
    block = re.search(r"LOCK_SPECS=\((?P<body>.*?)\)", script, re.DOTALL)
    assert block, "could not find LOCK_SPECS in scripts/lock-python-deps.sh"

    from_script = {}
    for entry in re.findall(r'"([^"]+)"', block["body"]):
        name, _, extras = entry.partition(":")
        from_script[name] = tuple(part for part in extras.split(",") if part)

    assert from_script == EXPECTED_LOCKS, (
        f"scripts/lock-python-deps.sh compiles {from_script}, but this test module "
        f"expects {EXPECTED_LOCKS}. Update both, and docs/PYTHON_DEPENDENCY_LOCKS.md."
    )
