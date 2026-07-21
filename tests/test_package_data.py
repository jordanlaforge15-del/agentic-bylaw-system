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
]


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
