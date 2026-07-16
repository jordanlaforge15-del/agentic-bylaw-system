"""Tests for competitive-intel schema validation."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from schema import (
    CompetitorProfile,
    Signal,
    validate_all,
    validate_file,
)

COMPETITORS_DIR = Path(__file__).resolve().parents[2] / "competitive-intel" / "competitors"
FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _minimal_competitor(**overrides) -> dict:
    base = {
        "name": "Test Corp",
        "slug": "test-corp",
        "url": "https://test.com",
        "category": "direct",
        "status": "active",
        "discovered": "2026-01-01",
        "last_analyzed": "2026-01-01",
        "description": "A test competitor.",
        "product": {
            "type": "saas",
            "target_market": ["developers"],
            "geography": ["United States"],
            "pricing_model": "subscription",
        },
        "threat_assessment": {
            "level": "low",
            "rationale": "Test rationale.",
        },
    }
    base.update(overrides)
    return base


class TestCompetitorProfile:
    def test_minimal_valid(self):
        data = _minimal_competitor()
        profile = CompetitorProfile.model_validate(data)
        assert profile.name == "Test Corp"
        assert profile.slug == "test-corp"
        assert profile.category == "direct"

    def test_all_fields(self):
        data = _minimal_competitor(
            positioning={
                "tagline": "Test tagline",
                "differentiators": ["Fast"],
                "weaknesses": ["Expensive"],
            },
            funding={
                "stage": "seed",
                "total_raised": "$1m",
                "notable_investors": ["Investor A"],
            },
            signals=[
                {
                    "date": "2026-05-01",
                    "type": "funding",
                    "summary": "Raised seed round",
                    "source_url": "https://example.com/news",
                }
            ],
        )
        profile = CompetitorProfile.model_validate(data)
        assert len(profile.signals) == 1
        assert profile.funding.stage == "seed"

    def test_invalid_category_rejected(self):
        data = _minimal_competitor(category="unknown")
        with pytest.raises(Exception):
            CompetitorProfile.model_validate(data)

    def test_invalid_slug_rejected(self):
        data = _minimal_competitor(slug="Has Spaces")
        with pytest.raises(Exception):
            CompetitorProfile.model_validate(data)

    def test_slug_trailing_dash_rejected(self):
        data = _minimal_competitor(slug="test-corp-")
        with pytest.raises(Exception):
            CompetitorProfile.model_validate(data)

    def test_empty_name_rejected(self):
        data = _minimal_competitor(name="")
        with pytest.raises(Exception):
            CompetitorProfile.model_validate(data)

    def test_invalid_date_rejected(self):
        data = _minimal_competitor(discovered="not-a-date")
        with pytest.raises(Exception):
            CompetitorProfile.model_validate(data)

    def test_invalid_threat_level_rejected(self):
        data = _minimal_competitor(
            threat_assessment={"level": "extreme", "rationale": "Test"}
        )
        with pytest.raises(Exception):
            CompetitorProfile.model_validate(data)

    def test_missing_product_rejected(self):
        data = _minimal_competitor()
        del data["product"]
        with pytest.raises(Exception):
            CompetitorProfile.model_validate(data)

    def test_empty_target_market_rejected(self):
        data = _minimal_competitor()
        data["product"]["target_market"] = []
        with pytest.raises(Exception):
            CompetitorProfile.model_validate(data)


class TestSignal:
    def test_valid_signal(self):
        s = Signal(
            date="2026-05-27",
            type="funding",
            summary="Raised $5M Series A",
        )
        assert s.type == "funding"

    def test_invalid_signal_type(self):
        with pytest.raises(Exception):
            Signal(date="2026-05-27", type="unknown-type", summary="Test")

    def test_invalid_date_format(self):
        with pytest.raises(Exception):
            Signal(date="May 27, 2026", type="funding", summary="Test")

    def test_empty_summary_rejected(self):
        with pytest.raises(Exception):
            Signal(date="2026-05-27", type="funding", summary="")


class TestValidateSeededFiles:
    """Validate the actual competitor files shipped with the repo."""

    def test_all_seeded_files_valid(self):
        errors = validate_all(COMPETITORS_DIR)
        assert errors == [], f"Seeded competitor files have errors: {errors}"

    @pytest.mark.parametrize(
        "yaml_file",
        sorted(COMPETITORS_DIR.glob("*.yaml")) if COMPETITORS_DIR.exists() else [],
        ids=lambda p: p.stem,
    )
    def test_individual_file(self, yaml_file: Path):
        profile = validate_file(yaml_file)
        assert profile.slug == yaml_file.stem, (
            f"slug {profile.slug!r} doesn't match filename {yaml_file.stem!r}"
        )

    @pytest.mark.parametrize(
        "yaml_file",
        sorted(COMPETITORS_DIR.glob("*.yaml")) if COMPETITORS_DIR.exists() else [],
        ids=lambda p: p.stem,
    )
    def test_signals_have_valid_types(self, yaml_file: Path):
        profile = validate_file(yaml_file)
        for signal in profile.signals:
            assert signal.type in (
                "product-launch",
                "feature-update",
                "funding",
                "partnership",
                "hiring",
                "press",
                "pricing-change",
                "geographic-expansion",
                "acquisition",
                "regulatory",
            )
