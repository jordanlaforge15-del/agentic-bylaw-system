"""Shared pytest fixtures for the abs-learning Phase 1 test suite.

Adds the project's ``src/`` directory to ``sys.path`` so tests can
import ``bootstrap``, ``manifest``, and ``agents`` packages directly.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
