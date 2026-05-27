"""Speckle Automate function inputs schema for the ABS compliance check."""
from __future__ import annotations

from pydantic import Field
from speckle_automate import AutomateBase


class FunctionInputs(AutomateBase):
    """Configuration provided by the Speckle workspace admin when registering
    the ABS compliance function.

    All fields are surfaced as a form in the Speckle Automate UI when the
    function is registered against an automation.
    """

    abs_api_url: str = Field(
        title="ABS API URL",
        description="Base URL of the ABS advisor API (e.g. https://api.yourdomain.com).",
    )
    abs_api_key: str = Field(
        title="ABS API Key",
        description=(
            "API key issued by ABS for this Speckle workspace. "
            "Keep this secret — treat it like a password."
        ),
    )
    parcel_id: int | None = Field(
        default=None,
        title="Parcel ID",
        description=(
            "Numeric ABS parcel ID for the site. Provide this OR parcel_address."
        ),
    )
    parcel_address: str | None = Field(
        default=None,
        title="Parcel Address / PID",
        description=(
            "Civic address or parcel identifier (PID) for the site. "
            "Used when parcel_id is not available."
        ),
    )
    run_evaluator: bool = Field(
        default=True,
        title="Run compliance evaluator",
        description=(
            "When True the function immediately runs the bylaw evaluator "
            "after ingestion and posts the compliance matrix as Automate "
            "output. When False it only ingests the model — useful for "
            "inspecting extracted attributes before a full evaluation."
        ),
    )
