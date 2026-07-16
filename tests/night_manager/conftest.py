# Override parent conftest — NM tests don't need sqlalchemy/layer2 fixtures.

# Pyproject's pythonpath puts `scripts/` itself on sys.path, which lets
# `from night_manager.X import Y` work but breaks the `from scripts.night_manager.X`
# form that every NM test module already uses. Prepend the repo root so the
# fully-qualified imports resolve without forcing a project-wide pyproject
# change.
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
