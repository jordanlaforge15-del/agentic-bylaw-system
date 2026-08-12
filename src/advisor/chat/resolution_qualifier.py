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

ABS-472 adds an orthogonal one: the address is real and the point is exact,
but the zone belongs to a by-law this corpus does not hold. HRM's zoning
layer is municipality-wide, so a downtown parcel comes back with a real zone
code whose standards live in a document we never ingested. Nothing in the
ABS-466 flags notices — the resolution is rooftop-perfect — so the net needs
its own read of ``governing_bylaw_status``.

ABS-469 adds the state above all of these: the civic number does not exist.
That profile carries no resolution quality at all — there is nothing to be
precise about — so the ABS-466 flags read it as a clean turn and the net
would let a fabricated address through silently. Its suffix outranks the
others and carries the civic numbers that do exist on the street, because
the correction is what makes the refusal useful.
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


# ABS-469: the address does not exist. Stronger than either suffix above,
# because there is no property to qualify an answer about — whatever the turn
# said about a zone came from a point the geocoder estimated on somebody
# else's parcel. The valid ranges are appended when the profile carried them,
# since a correction the user can act on is the whole point of the refusal.
_NONEXISTENT_SUFFIX = (
    "\n\n---\n\n"
    "**About this address:** {street_phrase} could not be found in the "
    "municipality's own address records — no civic address or street segment "
    "covers that number. Nothing above should be read as applying to a "
    "property at this address.{ranges} Please confirm the address before "
    "relying on any of it."
)


# ABS-472: the address is real, the zone is real, and the by-law that defines
# that zone is not in this corpus. Distinct from every suffix above: those say
# the point may be the wrong parcel's, this says the parcel is right and the
# rules are somewhere we cannot read. A turn that answered with standards
# anyway took them from a by-law that does not govern the property.
_GOVERNING_BYLAW_SUFFIX = (
    "\n\n---\n\n"
    "**About this property's by-law:** this parcel is governed by the "
    "{bylaw}, which is not part of the by-law corpus behind this answer. The "
    "zone code comes from HRM's published zoning mapping, but no standard "
    "under that by-law — permitted uses, height, setbacks, floor area — is "
    "available here, and the standards of the by-laws that are available do "
    "not apply to this parcel. Consult the {bylaw} directly, or confirm the "
    "requirements with HRM Planning & Development, before relying on any "
    "figure above."
)

# ABS-473: the narrower case. The parcel's zone may be held and correctly
# cited, and only an overlay over it — a height precinct, a FAR precinct —
# comes from a by-law we do not have. Worth its own wording because the
# suffix above would overclaim: saying "this parcel is governed by the
# Suburban Housing Accelerator LUB" is wrong when only its height precinct is.
_OVERLAY_GOVERNING_BYLAW_SUFFIX = (
    "\n\n---\n\n"
    "**About the {overlay} above:** it is mapped under the {bylaw}, which is "
    "not part of the by-law corpus behind this answer. The mapped value comes "
    "from HRM's published mapping, but the standard that applies it is not "
    "available here — and the equivalent schedule in the by-laws that are "
    "available does not govern this property. Consult the {bylaw} directly, "
    "or confirm the requirement with HRM Planning & Development, before "
    "relying on it."
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


def _address_payloads(tool_calls: Iterable[Any]) -> Iterable[dict[str, Any]]:
    """Yield each address tool's decoded compact payload, in call order.

    Errored calls and unparseable/non-object payloads are skipped — a
    malformed tool result must not manufacture a qualifier.
    """
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
            if isinstance(payload, dict):
                yield payload


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
    for payload in _address_payloads(tool_calls):
        if payload.get("outside_mapped_area"):
            outside = True
            imprecise = True
        quality = payload.get("resolution_quality")
        if isinstance(quality, str) and quality != "rooftop":
            imprecise = True
    return imprecise, outside


def nonexistent_address_suffix(tool_calls: Iterable[Any]) -> str | None:
    """The correction a turn owes the user when the address does not exist.

    Separate from ``address_resolution_flags`` because this is not a question
    of *precision*: the compact profile for a non-existent address carries no
    resolution quality at all — there is nothing to be precise about — so the
    ABS-466 flags read it as a clean turn. Returns None when no address tool
    reported ``civic_address_status: "not_found"`` this turn.
    """
    for payload in _address_payloads(tool_calls):
        if payload.get("civic_address_status") != "not_found":
            continue
        address = payload.get("address")
        street_phrase = (
            f"“{address}”" if isinstance(address, str) and address else "this address"
        )
        ranges = payload.get("valid_civic_number_ranges")
        ranges_phrase = ""
        if isinstance(ranges, list) and ranges:
            quoted = ", ".join(str(r) for r in ranges)
            ranges_phrase = (
                f" The civic numbers that do exist on that street are {quoted}."
            )
        return _NONEXISTENT_SUFFIX.format(
            street_phrase=street_phrase, ranges=ranges_phrase
        )
    return None


def governing_bylaw_suffix(tool_calls: Iterable[Any]) -> str | None:
    """The disclosure a turn owes when the governing by-law is not in the corpus.

    ABS-472. Returns None unless an address tool reported
    ``governing_bylaw_status: "not_held"`` with a named by-law — an unnamed
    one has nothing to tell the user to consult, so it falls through to the
    precision qualifiers rather than emitting a vague warning.
    """
    for payload in _address_payloads(tool_calls):
        if payload.get("governing_bylaw_status") == "not_held":
            bylaw = payload.get("governing_bylaw")
            if isinstance(bylaw, str) and bylaw.strip():
                return _GOVERNING_BYLAW_SUFFIX.format(bylaw=bylaw)
        # ABS-473: the zone-level status covers the zoning layer only. A
        # height precinct from an unheld by-law rides in the overlay list
        # with the zone still reading "held", and disclosed nothing.
        overlay_suffix = _unheld_overlay_suffix(payload)
        if overlay_suffix is not None:
            return overlay_suffix
    return None


def _unheld_overlay_suffix(payload: dict[str, Any]) -> str | None:
    """The disclosure owed by an overlay whose own by-law is not held."""
    overlays = payload.get("overlays")
    if not isinstance(overlays, list):
        return None
    for overlay in overlays:
        if not isinstance(overlay, dict) or overlay.get("governing_bylaw_held") is not False:
            continue
        bylaw = overlay.get("governing_bylaw")
        if not isinstance(bylaw, str) or not bylaw.strip():
            continue
        kind = overlay.get("kind")
        noun = kind.replace("_", " ") if isinstance(kind, str) and kind else "overlay"
        return _OVERLAY_GOVERNING_BYLAW_SUFFIX.format(overlay=noun, bylaw=bylaw)
    return None


def already_qualified(text: str) -> bool:
    """True when the answer already tells the user the match was imprecise."""
    low = text.lower()
    return any(marker in low for marker in _ALREADY_QUALIFIED_MARKERS)


# Markers that mean the answer already tells the user the address is not real
# (ABS-469). Narrower than ``_ALREADY_QUALIFIED_MARKERS``: a turn that hedges
# about precision has NOT said the address does not exist, and appending the
# refusal on top of a hedge is the correct outcome there.
_ALREADY_REFUSED_MARKERS: tuple[str, ...] = (
    "could not be found",
    "does not appear to exist",
    "does not exist",
    "no such address",
    "not a valid address",
    "no civic address",
)


def _already_refused(text: str) -> bool:
    low = text.lower()
    return any(marker in low for marker in _ALREADY_REFUSED_MARKERS)


# Markers that mean the answer already tells the user the governing by-law is
# not held (ABS-472). Narrower again than the two sets above: hedging about
# precision, or refusing the address outright, says nothing about WHICH by-law
# governs, so neither may suppress this disclosure.
_ALREADY_DISCLOSED_BYLAW_MARKERS: tuple[str, ...] = (
    "is not in this corpus",
    "not part of the by-law corpus",
    "not part of the bylaw corpus",
    "do not have the",
    "don't have the",
    "is not available here",
    "not held in",
)


def _already_disclosed_bylaw(text: str) -> bool:
    low = text.lower()
    return any(marker in low for marker in _ALREADY_DISCLOSED_BYLAW_MARKERS)


def apply_resolution_qualifier(
    content: list[ContentBlock], tool_calls: Iterable[Any]
) -> list[ContentBlock]:
    """Append a location-precision qualifier when the turn warrants one.

    No-op (returns the original list object, so callers can detect it with
    ``is``) when every address resolution this turn was rooftop-quality, when
    the turn touched no address tool, or when the answer already qualifies
    itself.
    """
    tool_calls = list(tool_calls)
    # ABS-469: a non-existent address outranks any precision qualifier — the
    # honest correction is that there is no such property, not that the point
    # behind it was estimated.
    nonexistent = nonexistent_address_suffix(tool_calls)
    # ABS-472: next after that, because it bounds what can be answered at all
    # rather than qualifying how well the parcel was found — a perfect rooftop
    # match on a parcel whose by-law we don't hold still cannot carry a
    # standard.
    governing = governing_bylaw_suffix(tool_calls) if nonexistent is None else None
    imprecise, outside = address_resolution_flags(tool_calls)
    if nonexistent is None and governing is None and not imprecise:
        return content
    text = "".join(b.text for b in content if isinstance(b, TextBlock))
    if not text.strip():
        return content
    if governing is not None and _already_disclosed_bylaw(text):
        governing = None
        if nonexistent is None and not imprecise:
            return content
    if nonexistent is None and governing is None and already_qualified(text):
        return content
    if nonexistent is not None and _already_refused(text):
        return content

    suffix = (
        nonexistent
        or governing
        or (_COVERAGE_SUFFIX if outside else _PRECISION_SUFFIX)
    )
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
