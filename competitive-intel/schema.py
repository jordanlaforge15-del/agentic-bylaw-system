"""Pydantic schema for competitor YAML files and CLI validator.

Usage:
    python competitive-intel/schema.py                  # validate all
    python competitive-intel/schema.py competitors/x.yaml  # validate one
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import List, Literal, Optional

import yaml
from pydantic import BaseModel, Field, field_validator


class ProductInfo(BaseModel):
    type: Literal["saas", "consulting", "platform", "marketplace", "open-source"]
    target_market: List[str] = Field(min_length=1)
    geography: List[str] = Field(min_length=1)
    jurisdictions: List[str] = []
    pricing_model: Literal[
        "subscription", "per-query", "freemium", "enterprise", "unknown"
    ]
    pricing_range: str = ""
    key_features: List[str] = []


class Positioning(BaseModel):
    tagline: str = ""
    differentiators: List[str] = []
    weaknesses: List[str] = []


class FundingInfo(BaseModel):
    stage: Literal[
        "pre-seed",
        "seed",
        "series-a",
        "series-b",
        "growth",
        "bootstrapped",
        "public",
        "unknown",
    ]
    total_raised: str = "unknown"
    last_round_date: Optional[str] = None
    notable_investors: List[str] = []


SIGNAL_TYPES = (
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


class Signal(BaseModel):
    date: str
    type: Literal[
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
    ]
    summary: str = Field(min_length=1)
    source_url: str = ""

    @field_validator("date")
    @classmethod
    def validate_date_format(cls, v: str) -> str:
        try:
            date.fromisoformat(v)
        except ValueError:
            raise ValueError(f"date must be ISO format (YYYY-MM-DD), got {v!r}")
        return v


class ThreatAssessment(BaseModel):
    level: Literal["high", "medium", "low"]
    rationale: str = Field(min_length=1)
    overlap_areas: List[str] = []
    watch_triggers: List[str] = []


class CompetitorProfile(BaseModel):
    name: str = Field(min_length=1)
    slug: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9-]*$")
    url: str = ""
    category: Literal["direct", "adjacent", "emerging"]
    status: Literal["active", "acquired", "defunct"] = "active"
    discovered: str
    last_analyzed: str
    description: str = Field(min_length=1)
    product: ProductInfo
    positioning: Positioning = Positioning()
    funding: FundingInfo = FundingInfo(stage="unknown")
    signals: List[Signal] = []
    threat_assessment: ThreatAssessment

    @field_validator("discovered", "last_analyzed")
    @classmethod
    def validate_date_fields(cls, v: str) -> str:
        try:
            date.fromisoformat(v)
        except ValueError:
            raise ValueError(f"must be ISO date (YYYY-MM-DD), got {v!r}")
        return v

    @field_validator("slug")
    @classmethod
    def slug_no_trailing_dash(cls, v: str) -> str:
        if v.endswith("-"):
            raise ValueError("slug must not end with a dash")
        return v


def validate_file(path: Path) -> CompetitorProfile:
    """Parse and validate a single competitor YAML file."""
    with open(path) as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a YAML mapping, got {type(data).__name__}")
    return CompetitorProfile.model_validate(data)


def validate_all(directory: Optional[Path] = None) -> List[str]:
    """Validate every *.yaml in the competitors directory. Returns error messages."""
    if directory is None:
        directory = Path(__file__).parent / "competitors"
    errors: List[str] = []
    yaml_files = sorted(directory.glob("*.yaml"))
    if not yaml_files:
        errors.append(f"No YAML files found in {directory}")
        return errors
    for path in yaml_files:
        try:
            profile = validate_file(path)
            expected_slug = path.stem
            if profile.slug != expected_slug:
                errors.append(
                    f"{path.name}: slug {profile.slug!r} doesn't match "
                    f"filename (expected {expected_slug!r})"
                )
        except Exception as exc:
            errors.append(f"{path.name}: {exc}")
    return errors


def main() -> int:
    args = sys.argv[1:]
    if args:
        paths = [Path(a) for a in args]
    else:
        paths = sorted((Path(__file__).parent / "competitors").glob("*.yaml"))

    if not paths:
        print("No competitor YAML files found.")
        return 1

    ok = 0
    fail = 0
    for path in paths:
        try:
            profile = validate_file(path)
            expected_slug = path.stem
            if profile.slug != expected_slug:
                print(f"FAIL  {path.name}: slug mismatch ({profile.slug!r} != {expected_slug!r})")
                fail += 1
            else:
                print(f"  OK  {path.name} ({profile.name})")
                ok += 1
        except Exception as exc:
            print(f"FAIL  {path.name}: {exc}")
            fail += 1

    print(f"\n{ok} passed, {fail} failed out of {ok + fail} files.")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
