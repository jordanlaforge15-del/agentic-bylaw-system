"""Answer-time qualifier for imprecise address resolutions (ABS-466).

``get_address_profile`` is the recommended first call on any case-bound
conversation with an address, and its ``zone`` drives every setback,
height and FAR answer that follows. When the point behind that zone was
*estimated* — interpolated along the street, a block centroid, or outside
every mapped boundary — the zone can be the neighbour's, and the user has
no way to know.

The profile now carries ``caveats`` and the tool_result carries an
``instruction`` telling the model to qualify. This module is the
deterministic safety net underneath both, in the same shape as the
ABS-263 feasibility hedge: after the tool loop finishes, look at what the
address tools actually returned this turn, and if the answer was built on
a below-rooftop resolution without saying so, append the qualification.
A user can then never receive a flat "your zone is HR-1" that was really
a guess, regardless of how the live model phrased its turn — and e2e can
assert the behaviour without depending on model wording.
"""
from __future__ import annotations

import json
from typing import Any, Iterable

from advisor.llm.base import ContentBlock, TextBlock

# Tools whose compact payload carries address-resolution quality.
ADDRESS_TOOLS: frozenset[str] = frozenset({"get_address_profile"})

# Markers that mean the answer already tells the user the location match is
# imprecise. Kept deliberately narrow — a generic "confirm with HRM" hedge
# (ABS-263) is about the *numbers*, not about which parcel we landed on, so
# it must NOT suppress this qualifier.
_ALREADY_QUALIFIED_MARKERS: tuple[str, ...] = (
    "approximate location",
    "could not be matched to a specific building",
    "did not resolve precisely",
    "estimated position",
    "interpolat",
    "may belong to a neighbouring parcel",
    "neighbouring parcel",
    "not matched to a specific building",
    "outside the mapped",
    "provisional",
)

_PRECISION_SUFFIX = (
    "\n\n---\n\n"
    "**About this address:** it could not be matched to a specific building. "
    "The location was estimated, so the zone above — and every setback, "
    "height and floor-area figure that follows from it — may belong to a "
    "neighbouring parcel. Confirm the property's zoning with HRM Planning & "
    "Development before relying on any of these numbers."
)

_COVERAGE_SUFFIX = (
    "\n\n---\n\n"
    "**About this address:** the location resolved, but it falls outside "
    "every zoning boundary mapped in this corpus, so no zone could be "
    "assigned to it. Nothing above should be read as a zoning determination "
    "for this property — confirm the address and its zone with HRM Planning "
    "& Development."
)


def _payload_texts(output: Any) -> Iterable[str]:
    """Yield every string payload in a tool handler's return value."""
    if isinstance(output, str):
        yield output
        return
    if isinstance(output, list):
        for block in output:
            text = getattr(block, "text", None)
            if isinstance(text, str):
                yield text


def address_resolution_flags(tool_calls: Iterable[Any]) -> tuple[bool, bool]:
    """Inspect a turn's tool calls for imprecise address resolutions.

    Returns ``(imprecise, outside_mapped_area)``. ``imprecise`` is True when
    any address tool returned a resolution below rooftop quality;
    ``outside_mapped_area`` when a point resolved but matched no mapped
    boundary. Unparseable or unrelated payloads are ignored — a malformed
    tool result must not manufacture a qualifier.
    """
    imprecise = False
    outside = False
    for call in tool_calls:
        if getattr(call, "tool_name", None) not in ADDRESS_TOOLS:
            continue
        if getattr(call, "error", None):
            continue
        for text in _payload_texts(getattr(call, "output", None)):
            try:
                payload = json.loads(text)
            except (TypeError, ValueError):
                continue
            if not isinstance(payload, dict):
                continue
            if payload.get("outside_mapped_area"):
                outside = True
                imprecise = True
            quality = payload.get("resolution_quality")
            if isinstance(quality, str) and quality != "rooftop":
                imprecise = True
    return imprecise, outside


def already_qualified(text: str) -> bool:
    """True when the answer already tells the user the match was imprecise."""
    low = text.lower()
    return any(marker in low for marker in _ALREADY_QUALIFIED_MARKERS)


def apply_resolution_qualifier(
    content: list[ContentBlock], tool_calls: Iterable[Any]
) -> list[ContentBlock]:
    """Append a location-precision qualifier when the turn warrants one.

    No-op (returns the original list object, so callers can detect it with
    ``is``) when every address resolution this turn was rooftop-quality, when
    the turn touched no address tool, or when the answer already qualifies
    itself.
    """
    imprecise, outside = address_resolution_flags(tool_calls)
    if not imprecise:
        return content
    text = "".join(b.text for b in content if isinstance(b, TextBlock))
    if not text.strip() or already_qualified(text):
        return content

    suffix = _COVERAGE_SUFFIX if outside else _PRECISION_SUFFIX
    new_content = list(content)
    for i in range(len(new_content) - 1, -1, -1):
        block = new_content[i]
        if isinstance(block, TextBlock):
            new_content[i] = block.model_copy(
                update={"text": block.text + suffix}
            )
            return new_content
    new_content.append(TextBlock(text=suffix.lstrip()))
    return new_content
