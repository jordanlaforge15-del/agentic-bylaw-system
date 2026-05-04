from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _disable_external_google_geocoder(monkeypatch):
    """Make the Google Maps fallback unreachable from any test by default.

    Tests must never depend on the presence/absence of a real API key on
    disk or on Google's network availability. Phase G tests opt in
    explicitly by passing ``google_geocoder=`` to ``resolve_location`` (which
    bypasses the auto-builder this fixture stubs out).
    """
    import layer2.retrieval.geocode as geocode_module

    monkeypatch.setattr(geocode_module, "_maybe_build_google_geocoder", lambda: None)
