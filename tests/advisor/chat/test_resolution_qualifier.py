"""ABS-466 — the advisor visibly qualifies an answer built on an imprecise
address resolution.

The failure this guards: a user's address interpolates onto the parcel
across a zone line, ``get_address_profile`` returns HR-1, and the advisor
states the zone (and every setback/height/FAR figure derived from it) as
fact. The profile now carries the quality, the compact projection passes it
to the model, and — regardless of how the model phrases its turn — the
deterministic qualifier makes sure the user is told the location was
estimated.
"""
from __future__ import annotations

import json

import pytest

from advisor.chat.compact import compact_address_profile
from advisor.chat.resolution_qualifier import (
    address_resolution_flags,
    already_qualified,
    apply_resolution_qualifier,
    governing_bylaw_suffix,
    nonexistent_address_suffix,
)
from advisor.chat.session import ChatSession
from advisor.llm.base import TextBlock
from advisor.llm.mock import MockGateway, text_response, tool_use_response
from advisor.llm.tool_loop import ToolInvocation
from bylaw_retrieval.retrieval.schemas import AddressProfile


def _profile(**kwargs) -> AddressProfile:
    """An AddressProfile for a resolved address, defaults to rooftop."""
    base = dict(
        address="1234 Oxford Street",
        civic_number="1234",
        street="Oxford Street",
        zone="HR-1",
        resolution_quality="rooftop",
        location_confidence=0.95,
        location_type="ROOFTOP",
        location_resolver="google_maps",
    )
    base.update(kwargs)
    return AddressProfile(**base)


def _interpolated_profile() -> AddressProfile:
    from layer2.retrieval.resolution_quality import resolution_caveat

    return _profile(
        resolution_quality="interpolated",
        location_confidence=0.85,
        location_type="RANGE_INTERPOLATED",
        caveats=[resolution_caveat("interpolated")],
    )


def _invocation(profile: AddressProfile) -> ToolInvocation:
    return ToolInvocation(
        tool_use_id="tu_1",
        tool_name="get_address_profile",
        input={"address": profile.address},
        output=json.dumps(compact_address_profile(profile)),
    )


# --- the compact projection the model actually reads ----------------------


def test_compact_projection_carries_quality_for_a_rooftop_match():
    out = compact_address_profile(_profile())
    assert out["zone"] == "HR-1"
    assert out["resolution_quality"] == "rooftop"
    # Nothing to qualify -> no caveats, no instruction to hedge.
    assert "caveats" not in out
    assert "instruction" not in out


def test_compact_projection_tells_the_model_to_qualify_an_estimate():
    out = compact_address_profile(_interpolated_profile())
    assert out["resolution_quality"] == "interpolated"
    assert out["location_confidence"] == 0.85
    assert out["caveats"]
    assert "do not present the zone" in out["instruction"].lower()


def test_compact_projection_flags_a_point_outside_the_mapped_area():
    profile = _profile(
        zone=None,
        outside_mapped_area=True,
        caveats=["The address resolved to a point, but that point falls outside …"],
    )
    out = compact_address_profile(profile)
    assert out["outside_mapped_area"] is True
    assert "do not state a zone" in out["instruction"].lower()


# --- the deterministic qualifier -----------------------------------------


def test_flags_are_false_for_a_rooftop_turn():
    assert address_resolution_flags([_invocation(_profile())]) == (False, False)


def test_flags_detect_an_interpolated_turn():
    assert address_resolution_flags([_invocation(_interpolated_profile())]) == (
        True,
        False,
    )


def test_flags_detect_an_outside_coverage_turn():
    profile = _profile(zone=None, outside_mapped_area=True, caveats=["…"])
    assert address_resolution_flags([_invocation(profile)]) == (True, True)


def test_unparseable_or_errored_tool_output_never_manufactures_a_qualifier():
    """A malformed payload must not invent uncertainty, and an errored call
    has no resolution to judge."""
    junk = ToolInvocation(
        tool_use_id="tu_x",
        tool_name="get_address_profile",
        input={},
        output="not json at all",
    )
    errored = ToolInvocation(
        tool_use_id="tu_y",
        tool_name="get_address_profile",
        input={},
        output=None,
        error="boom",
    )
    other_tool = ToolInvocation(
        tool_use_id="tu_z",
        tool_name="search_bylaw_evidence",
        input={},
        output='{"resolution_quality": "interpolated"}',
    )
    assert address_resolution_flags([junk, errored, other_tool]) == (False, False)


def test_qualifier_is_a_noop_for_rooftop_turns():
    content = [TextBlock(text="Your zone is HR-1; max height is 25 m.")]
    assert apply_resolution_qualifier(content, [_invocation(_profile())]) is content


def test_qualifier_appends_precision_note_for_an_interpolated_turn():
    answer = "Your property is zoned HR-1. Max height is 25 m."
    out = apply_resolution_qualifier(
        [TextBlock(text=answer)], [_invocation(_interpolated_profile())]
    )
    assert out[0].text.startswith(answer)
    low = out[0].text.lower()
    assert "could not be matched to a specific building" in low
    assert "neighbouring parcel" in low


def test_qualifier_uses_the_coverage_wording_when_outside_the_mapped_area():
    profile = _profile(zone=None, outside_mapped_area=True, caveats=["…"])
    out = apply_resolution_qualifier(
        [TextBlock(text="No zone is recorded for this address.")], [_invocation(profile)]
    )
    assert "outside" in out[0].text.lower()
    assert "no zone could be assigned" in out[0].text.lower()


def test_qualifier_does_not_double_up_on_an_answer_that_already_says_it():
    answer = (
        "I could not match this address to a specific building — the position "
        "was estimated, so the zone may belong to a neighbouring parcel."
    )
    assert already_qualified(answer) is True
    content = [TextBlock(text=answer)]
    assert (
        apply_resolution_qualifier(content, [_invocation(_interpolated_profile())])
        is content
    )


def test_generic_feasibility_hedge_does_not_suppress_the_precision_note():
    """The ABS-263 hedge qualifies the NUMBERS ('confirm with a planner').
    It says nothing about WHICH PARCEL they were computed for, so it must
    not stand in for this qualifier."""
    answer = (
        "Your zone is HR-1, max height 25 m. Confirm the figures with HRM "
        "Planning & Development or a qualified planner before proceeding."
    )
    assert already_qualified(answer) is False
    content = [TextBlock(text=answer)]
    out = apply_resolution_qualifier(content, [_invocation(_interpolated_profile())])
    assert out is not content
    assert "could not be matched to a specific building" in out[0].text.lower()


# --- end to end through a chat turn --------------------------------------


@pytest.mark.asyncio
async def test_interpolated_address_turn_is_qualified_end_to_end():
    """Drive a full turn: the model calls get_address_profile for an
    interpolated address, the real compact projection comes back, and the
    model answers with a flat zone statement. The user must not receive that
    answer unqualified."""
    session = ChatSession(
        session_id="sess_abs466",
        user_id="user_abs466",
        system_prompt="You are a senior urban planner.",
        model="claude-opus-4-5",
    )
    profile = _interpolated_profile()

    async def address_handler(payload: dict) -> str:
        return json.dumps(compact_address_profile(profile))

    session.tool_handlers = {"get_address_profile": address_handler}

    flat_answer = "Your property at 1234 Oxford Street is zoned HR-1."
    gateway = MockGateway(
        scripted=[
            tool_use_response(
                tool_id="tu_1",
                tool_name="get_address_profile",
                tool_input={"address": "1234 Oxford Street"},
            ),
            text_response(flat_answer),
        ]
    )

    response = await session.send_user_message_blocking(
        gateway, "What's the zoning at 1234 Oxford Street?"
    )

    text = response.content[-1].text
    assert text.startswith(flat_answer)
    low = text.lower()
    assert "could not be matched to a specific building" in low
    assert "neighbouring parcel" in low
    assert "hrm" in low
    # The persisted assistant turn carries the same qualified text, so the
    # transcript and the user see the same thing.
    assert session.messages[-1].content[-1].text == text


@pytest.mark.asyncio
async def test_rooftop_address_turn_stays_lean():
    """The carve-out: a precise match must NOT get the qualifier bolted on."""
    session = ChatSession(
        session_id="sess_abs466_b",
        user_id="user_abs466",
        system_prompt="You are a senior urban planner.",
        model="claude-opus-4-5",
    )

    async def address_handler(payload: dict) -> str:
        return json.dumps(compact_address_profile(_profile()))

    session.tool_handlers = {"get_address_profile": address_handler}

    answer = "Your property at 1234 Oxford Street is zoned HR-1."
    gateway = MockGateway(
        scripted=[
            tool_use_response(
                tool_id="tu_1",
                tool_name="get_address_profile",
                tool_input={"address": "1234 Oxford Street"},
            ),
            text_response(answer),
        ]
    )

    response = await session.send_user_message_blocking(
        gateway, "What's the zoning at 1234 Oxford Street?"
    )
    assert response.content[-1].text == answer


# --- ABS-469: the address does not exist ----------------------------------


def _nonexistent_profile() -> AddressProfile:
    """What the profile returns for a civic number no street segment carries."""
    return AddressProfile(
        address="567 Windsor Street",
        civic_number="567",
        street="Windsor Street",
        civic_address_status="not_found",
        civic_address_evidence="street_centerline_ranges (halifax_street_centerlines)",
        valid_civic_number_ranges=["2001-3799"],
        suggested_civic_numbers=["2001"],
        caveats=["This civic number does not exist. …"],
    )


def test_a_nonexistent_address_carries_no_resolution_quality_to_flag():
    """Why this state needs its own detector.

    The ABS-466 flags look for a below-rooftop ``resolution_quality`` or an
    ``outside_mapped_area`` marker. A non-existent address has neither — it
    was never geocoded — so the precision net reads the turn as clean and the
    fabricated address would pass through it silently.
    """
    invocation = _invocation(_nonexistent_profile())
    assert address_resolution_flags([invocation]) == (False, False)
    assert nonexistent_address_suffix([invocation]) is not None


def test_the_refusal_is_appended_with_the_numbers_that_do_exist():
    content = [TextBlock(text="567 Windsor Street is zoned HR-2, with a 2.5 m side yard.")]
    out = apply_resolution_qualifier(content, [_invocation(_nonexistent_profile())])

    assert out is not content
    appended = out[-1].text.lower()
    assert "could not be found" in appended
    # The correction, not just the rejection.
    assert "2001-3799" in out[-1].text


def test_the_refusal_outranks_the_precision_hedge():
    """An answer that hedges about precision has still not said the address
    is not real, so the refusal is appended over the top of it."""
    profile = _nonexistent_profile()
    content = [
        TextBlock(
            text=(
                "This address did not resolve precisely to the property, so "
                "the zone may belong to a neighbouring parcel."
            )
        )
    ]
    out = apply_resolution_qualifier(content, [_invocation(profile)])

    assert "could not be found in the municipality" in out[-1].text


def test_an_answer_that_already_refuses_is_left_alone():
    content = [
        TextBlock(
            text=(
                "There is no 567 Windsor Street — that civic number does not "
                "exist. Valid numbers on that street run 2001-3799."
            )
        )
    ]
    out = apply_resolution_qualifier(content, [_invocation(_nonexistent_profile())])

    assert out is content


# --- ABS-472: the governing by-law is not in the corpus -------------------


def _unheld_bylaw_profile(**kwargs) -> AddressProfile:
    """A rooftop-perfect resolution on ground governed by a by-law we lack.

    1657 Barrington Street: DH-1 is a Downtown Halifax LUB zone, and the
    corpus holds no Downtown Halifax document. The zone code is HRM's own
    published mapping and is correct; every standard behind it is somewhere
    we cannot read.
    """
    base = dict(
        address="1657 Barrington Street",
        civic_number="1657",
        street="Barrington Street",
        zone="DH-1",
        resolution_quality="rooftop",
        location_confidence=0.95,
        location_type="ROOFTOP",
        location_resolver="google_maps",
        governing_bylaw="Downtown Halifax Land Use By-law",
        governing_bylaw_code="hrm:DHFX",
        governing_bylaw_status="not_held",
        caveats=[
            "This parcel is zoned DH-1 under the Downtown Halifax Land Use "
            "By-law, which is NOT in this corpus. …"
        ],
    )
    base.update(kwargs)
    return AddressProfile(**base)


def test_compact_projection_names_the_governing_bylaw_and_forbids_standards():
    out = compact_address_profile(_unheld_bylaw_profile())
    assert out["zone"] == "DH-1"
    assert out["governing_bylaw"] == "Downtown Halifax Land Use By-law"
    assert out["governing_bylaw_status"] == "not_held"
    instruction = out["instruction"].lower()
    assert "downtown halifax land use by-law" in instruction
    assert "do not give permitted uses" in instruction


def test_compact_projection_stays_lean_when_the_bylaw_is_held():
    """A held by-law is the normal case and must not add an instruction."""
    out = compact_address_profile(
        _profile(governing_bylaw="Regional Centre Land Use By-law", governing_bylaw_status="held")
    )
    assert out["governing_bylaw_status"] == "held"
    assert "instruction" not in out


def test_an_unheld_bylaw_is_invisible_to_the_precision_flags():
    """Why this needs its own detector: the resolution is rooftop-perfect, so
    every ABS-466/469 signal reads the turn as clean."""
    invocation = _invocation(_unheld_bylaw_profile())
    assert address_resolution_flags([invocation]) == (False, False)
    assert nonexistent_address_suffix([invocation]) is None
    assert governing_bylaw_suffix([invocation]) is not None


def test_the_bylaw_disclosure_is_appended_to_a_confident_answer():
    content = [
        TextBlock(
            text=(
                "1657 Barrington Street is zoned DH-1, which permits a "
                "maximum height of 27 m and no side-yard setback."
            )
        )
    ]
    out = apply_resolution_qualifier(content, [_invocation(_unheld_bylaw_profile())])

    assert out is not content
    appended = out[-1].text
    assert "Downtown Halifax Land Use By-law" in appended
    assert "not part of the by-law corpus" in appended


def test_the_bylaw_disclosure_outranks_the_precision_hedge():
    """An interpolated point on unheld ground: both are true, but the by-law
    gap bounds what can be answered at all, so it is the one appended."""
    profile = _unheld_bylaw_profile(
        resolution_quality="interpolated",
        location_type="RANGE_INTERPOLATED",
        location_confidence=0.85,
    )
    out = apply_resolution_qualifier([TextBlock(text="Zoned DH-1.")], [_invocation(profile)])

    assert "Downtown Halifax Land Use By-law" in out[-1].text


def test_a_nonexistent_address_still_outranks_the_bylaw_disclosure():
    """There is no property to name a governing by-law for."""
    profile = _nonexistent_profile()
    profile.governing_bylaw = "Downtown Halifax Land Use By-law"
    profile.governing_bylaw_status = "not_held"
    out = apply_resolution_qualifier(
        [TextBlock(text="1657 Barrington Street is zoned DH-1.")], [_invocation(profile)]
    )

    assert "could not be found in the municipality" in out[-1].text
    assert "not part of the by-law corpus" not in out[-1].text


def test_an_answer_that_already_discloses_the_gap_is_left_alone():
    content = [
        TextBlock(
            text=(
                "1657 Barrington Street is zoned DH-1 under the Downtown "
                "Halifax Land Use By-law, which is not in this corpus — I "
                "can't give you its standards."
            )
        )
    ]
    out = apply_resolution_qualifier(content, [_invocation(_unheld_bylaw_profile())])

    assert out is content


def test_a_held_bylaw_adds_nothing():
    invocation = _invocation(
        _profile(governing_bylaw="Regional Centre Land Use By-law", governing_bylaw_status="held")
    )
    assert governing_bylaw_suffix([invocation]) is None
    content = [TextBlock(text="Zoned HR-1.")]
    assert apply_resolution_qualifier(content, [invocation]) is content


@pytest.mark.asyncio
async def test_unheld_bylaw_turn_is_disclosed_end_to_end():
    """The whole point: a user asking about 1657 Barrington gets DH-1 plus
    dimensional standards reasoned out of the wrong by-law. Whatever the model
    says, the turn must tell them which by-law governs and that we don't hold
    it."""
    session = ChatSession(
        session_id="sess_abs472",
        user_id="user_abs472",
        system_prompt="You are a senior urban planner.",
        model="claude-opus-4-5",
    )
    profile = _unheld_bylaw_profile()

    async def address_handler(payload: dict) -> str:
        return json.dumps(compact_address_profile(profile))

    session.tool_handlers = {"get_address_profile": address_handler}

    flat_answer = (
        "1657 Barrington Street is zoned DH-1. Maximum height is 27 m and no "
        "side-yard setback is required."
    )
    gateway = MockGateway(
        scripted=[
            tool_use_response(
                tool_id="tu_1",
                tool_name="get_address_profile",
                tool_input={"address": "1657 Barrington Street"},
            ),
            text_response(flat_answer),
        ]
    )

    response = await session.send_user_message_blocking(
        gateway, "What can I build at 1657 Barrington Street?"
    )

    text = response.content[-1].text
    assert text.startswith(flat_answer)
    assert "Downtown Halifax Land Use By-law" in text
    assert "hrm planning" in text.lower()
    assert session.messages[-1].content[-1].text == text
