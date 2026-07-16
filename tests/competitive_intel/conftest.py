"""Add competitive-intel/ to the import path so schema.py is importable."""
from __future__ import annotations

import sys
from pathlib import Path

_CI_DIR = Path(__file__).resolve().parents[2] / "competitive-intel"
if str(_CI_DIR) not in sys.path:
    sys.path.insert(0, str(_CI_DIR))
