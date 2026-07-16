"""ABS-80 regression: pyproj must be importable via the [advisor] extra.

After the Phase-2 BIM ingestion merge (b19940c), advisor.api.submissions_router
unconditionally imports layer2.compliance.derived_attributes, which in turn does
a top-level `from pyproj import Transformer`. With pyproj only in [bim], every
API boot crashed with ModuleNotFoundError before the advisor port ever opened.

The fix (8f6a57c) moved pyproj into [advisor]. These tests pin the import chain
so a future extras reshuffle can't silently break startup again.
"""
from __future__ import annotations

import importlib
import sys


def _reload(module_name: str) -> None:
    """Force a fresh import, bypassing sys.modules cache."""
    sys.modules.pop(module_name, None)
    importlib.import_module(module_name)


def test_pyproj_importable():
    """pyproj must be available — it is now an unconditional advisor dep."""
    import pyproj  # noqa: F401 — existence check is the assertion


def test_derived_attributes_importable():
    """layer2.compliance.derived_attributes does a top-level pyproj import."""
    import layer2.compliance.derived_attributes  # noqa: F401


def test_submissions_router_importable():
    """advisor.api.submissions_router imports derived_attributes at module load."""
    import advisor.api.submissions_router  # noqa: F401


def test_create_app_does_not_raise():
    """Calling create_app() (the path e2e_server takes) must not raise."""
    from advisor.api import InMemorySessionStore, create_app
    from advisor.llm.mock import MockGateway, text_response

    app = create_app(
        gateway=MockGateway(scripted=[text_response("ok")]),
        retrieval_service_factory=lambda: None,
        session_store=InMemorySessionStore(),
        persona_text="test",
    )
    assert app is not None
