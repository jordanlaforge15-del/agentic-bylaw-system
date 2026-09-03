"""Packaging regression tests for runtime data files (ABS-412).

The advisor image installs this project as a wheel (``pip install
".[advisor]"`` in Dockerfile.advisor), so any file a module resolves
relative to itself — via ``importlib.resources`` or ``Path(__file__)`` —
must be declared under ``[tool.setuptools.package-data]`` or it exists in
the repo checkout but not in the deployed image. That is how the ABS-409
prod heal broke: ``layer1/semantic/taxonomy.json`` was missing from
site-packages and enrichment died with ``FileNotFoundError``.

Two layers of defence:

* ``test_wheel_contains_runtime_data_files`` builds a real wheel and
  asserts every module-relative data file is inside it — the honest
  end-to-end check of the package-data declaration.
* The loader smoke tests pin each module-relative read path, so a file
  or package rename that silently invalidates the declaration surfaces
  here rather than in prod.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# What Dockerfile.advisor COPYs before `pip install .` — the wheel build must
# be exercised against exactly this context. Building in the repo checkout
# instead is unfaithful two ways: a stale in-tree `build/` directory from an
# earlier build leaks files into the wheel that the declaration no longer
# covers, which is precisely how the missing-taxonomy.json bug stayed
# invisible locally while breaking the deployed image.
DOCKER_BUILD_CONTEXT = ["pyproject.toml", "README.md", "src", "mcp"]

# Every non-.py file that installed code resolves module-relative. Adding a
# new module-relative asset means adding it here AND to
# [tool.setuptools.package-data] in pyproject.toml.
REQUIRED_WHEEL_DATA_FILES = [
    "layer1/semantic/taxonomy.json",
    "layer2/prompts/assets/system_v1.txt",
    "layer2/compliance/attributes/taxonomy.yaml",
    # ABS-420: the corpus-coherence audit's declaration set. Two entries are
    # named literally — a config, and the shared lookup table two configs
    # pull in with `lookups_from:` — so a rename of either directory fails
    # here; the whole set is checked by the two tests below.
    "layer1/datasets/halifax_zoning.yaml",
    "layer1/datasets/lookups/hrm_bylaw_areas.yaml",
]

DATASET_CONFIG_DIR = REPO_ROOT / "src" / "layer1" / "datasets"


@pytest.fixture(scope="module")
def built_wheel(tmp_path_factory: pytest.TempPathFactory) -> Path:
    src_dir = tmp_path_factory.mktemp("context")
    for entry in DOCKER_BUILD_CONTEXT:
        source = REPO_ROOT / entry
        if source.is_dir():
            shutil.copytree(
                source,
                src_dir / entry,
                ignore=shutil.ignore_patterns("__pycache__", "*.egg-info"),
            )
        else:
            shutil.copy2(source, src_dir / entry)

    out_dir = tmp_path_factory.mktemp("wheel")
    # --no-build-isolation: use the venv's setuptools instead of resolving a
    # fresh build env, so the test never touches the network.
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(out_dir),
            str(src_dir),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.fail(f"pip wheel failed:\n{result.stdout}\n{result.stderr}")
    wheels = list(out_dir.glob("*.whl"))
    assert len(wheels) == 1, f"expected exactly one wheel, got {wheels}"
    return wheels[0]


def test_wheel_contains_runtime_data_files(built_wheel: Path) -> None:
    with zipfile.ZipFile(built_wheel) as wheel:
        names = set(wheel.namelist())
    missing = [name for name in REQUIRED_WHEEL_DATA_FILES if name not in names]
    assert not missing, (
        f"wheel is missing runtime data files: {missing} — declare them in "
        "[tool.setuptools.package-data] in pyproject.toml, or the deployed "
        "advisor image will FileNotFoundError at runtime (ABS-412)"
    )


def test_wheel_contains_every_dataset_config(built_wheel: Path) -> None:
    """Every layer1 dataset YAML must ship, not just the ones named above.

    The corpus-coherence audit (ABS-356) reads this whole directory as its
    declaration set: a config that exists in the checkout but not in the
    wheel is a role the deployed advisor silently stops checking. Absent the
    entire directory — the state every image before ABS-420 shipped — it
    checked nothing and reported ``{"status":"ok","checked_roles":0}``.
    """
    expected = {f"layer1/datasets/{path.name}" for path in DATASET_CONFIG_DIR.glob("*.yaml")}
    assert expected, f"no dataset configs found under {DATASET_CONFIG_DIR}"
    with zipfile.ZipFile(built_wheel) as wheel:
        names = set(wheel.namelist())
    missing = sorted(expected - names)
    assert not missing, (
        f"wheel is missing dataset configs: {missing} — /v1/monitoring/"
        "corpus-coherence would stop checking those overlay roles in the "
        "deployed image while still reporting green (ABS-420)"
    )


def test_declarations_load_from_an_installed_wheel(
    built_wheel: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Install the wheel and load the declarations out of it, as the image does.

    File-presence checks are not enough. The configs are shipped *and*
    unreadable if a file they reference is not: two of them pull a shared
    by-law-area table with ``lookups_from: lookups/hrm_bylaw_areas.yaml``,
    which a ``*.yaml`` package-data glob does not cover, and the audit then
    raises a ValueError one directory deeper than the check would look
    (ABS-420). Only an install proves the deployed advisor can read them.
    """
    target = tmp_path_factory.mktemp("install")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--no-deps", "--target", str(target), str(built_wheel)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.fail(f"pip install --target failed:\n{result.stdout}\n{result.stderr}")

    probe = (
        "import sys; sys.path.insert(0, sys.argv[1]);"
        "from bylaw_retrieval.retrieval.coherence_audit import "
        "DEFAULT_DATASET_CONFIG_DIR as d, load_overlay_declarations as load;"
        "assert str(d).startswith(sys.argv[1]), d;"
        "print(len(load()))"
    )
    loaded = subprocess.run(
        [sys.executable, "-c", probe, str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert loaded.returncode == 0, (
        "the installed wheel cannot load its overlay declarations — the deployed "
        f"advisor's corpus-coherence audit would fail the same way:\n{loaded.stderr}"
    )
    expected = len(list(DATASET_CONFIG_DIR.glob("*.yaml"))) - _role_bearing_config_count()
    assert int(loaded.stdout.strip()) == expected


def _role_bearing_config_count() -> int:
    """Configs the audit skips: those with a ``role`` instead of a ``links_to``."""
    return sum(
        1
        for path in DATASET_CONFIG_DIR.glob("*.yaml")
        if any(line.startswith("role:") for line in path.read_text().splitlines())
    )


def test_dataset_config_dir_follows_the_installed_package() -> None:
    """The audit's config dir must be the package dir, not a repo-relative walk.

    ``Path(__file__).parents[3] / "src" / "layer1" / "datasets"`` resolved to
    ``/opt/venv/lib/python3.11/src/layer1/datasets`` inside the advisor image
    — a path no install creates. Pinning it to the package directory is what
    makes the packaged YAMLs above reachable at runtime (ABS-420).
    """
    from layer1 import datasets as layer1_datasets

    from bylaw_retrieval.retrieval.coherence_audit import DEFAULT_DATASET_CONFIG_DIR

    assert DEFAULT_DATASET_CONFIG_DIR == Path(layer1_datasets.__file__).resolve().parent
    assert "site-packages" in str(DEFAULT_DATASET_CONFIG_DIR) or DEFAULT_DATASET_CONFIG_DIR == (
        DATASET_CONFIG_DIR
    )


def test_missing_config_dir_raises_instead_of_auditing_nothing(tmp_path: Path) -> None:
    """An unreadable declaration set is an error, never an empty audit (ABS-420)."""
    from bylaw_retrieval.retrieval.coherence_audit import load_overlay_declarations

    with pytest.raises(FileNotFoundError, match="does not exist"):
        load_overlay_declarations(tmp_path / "not-installed" / "datasets")


def test_taxonomy_json_loads_via_importlib_resources() -> None:
    from layer1.semantic.taxonomy import load_taxonomy

    load_taxonomy.cache_clear()
    taxonomy = load_taxonomy()
    assert taxonomy["entity_types"]["standard"]["terms"]
    assert taxonomy["entity_types"]["use"]["suffixes"]


def test_system_prompt_asset_readable_from_module_relative_path() -> None:
    from layer2.prompts.builder import load_system_prompt

    assert load_system_prompt()


def test_compliance_taxonomy_readable_from_module_relative_path() -> None:
    from layer2.compliance.taxonomy import load_taxonomy

    assert load_taxonomy(reload=True).attributes
