"""ABS-479 — ``attribute_tag_filter`` on the LLM-facing tool surfaces.

Background: the GIN index on ``source_fragment.attribute_tags`` (migration
0014) is the only indexed retrieval pre-filter in the repo, and until this
issue it was reachable ONLY from the compliance evaluator. The MCP server
accepted the argument, but neither the chat tool schema
(``advisor.chat.tools``) nor the OpenAI-shaped spec
(``bylaw_retrieval.openai_tools``) advertised it, so no LLM could ever ask
for it.

Three behaviours are pinned here:

* The chat handler forwards the filter and it actually narrows the result
  set through the indexed clause (exercised against a real sqlite-backed
  ``RetrievalService``, not a mock, so a schema/handler drift fails loudly).
* An EMPTY list is rejected cleanly and reaches the model as a tool error
  rather than silently degrading into "no filter" (or a 500).
* The chat-shaped and OpenAI-shaped specs stay in step — the drift the
  ABS-469 fix (``fe705e3``) had to clean up after ABS-466.
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from advisor.chat.tools import build_bylaw_tools
from advisor.llm.tool_loop import ToolUseBlock, _run_one_handler
from bylaw_retrieval.openai_tools import (
    ATTRIBUTE_TAG_FILTER_PROPERTY,
    build_openai_responses_tool_specs,
)
from bylaw_retrieval.retrieval import RetrievalService
from layer1.db.base import Document, SourceFragment
from layer1.db.init_db import create_all
from layer1.db.session import session_scope
from layer1.models.enums import FragmentType, ParseStatus

TAXONOMY_PATH = Path("src/layer2/compliance/attributes/taxonomy.yaml")

# tests/advisor/chat/<this file> -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
REPO_MCP_SERVER_PATH = REPO_ROOT / "mcp" / "bylaw_retrieval" / "server.py"


@pytest.fixture()
def tagged_service(tmp_path: Path):
    """A ``RetrievalService`` over three clauses with distinct attribute tags.

    The corpus is built so the *text* query alone cannot separate the
    clauses — every fragment mentions both "height" and "setback". Only the
    tag pre-filter can, which is what makes the narrowing assertion mean
    something.
    """
    db_url = f"sqlite:///{tmp_path / 'attr_filter_tools.db'}"
    create_all(db_url)

    with session_scope(db_url) as session:
        document = Document(
            municipality="Sampleton",
            bylaw_name="Attribute Tag Bylaw",
            source_path="attr.pdf",
            file_hash="attr-hash",
            mime_type="application/pdf",
            page_count=3,
            parser_version="test",
        )
        session.add(document)
        session.flush()
        for citation_path, page, tags in (
            ("4.2.1", 1, ["front_setback_m"]),
            ("4.3.1", 2, ["building_height_m"]),
            ("4.4.1", 3, ["lot_coverage_percent"]),
        ):
            session.add(
                SourceFragment(
                    document_id=document.id,
                    fragment_type=FragmentType.CLAUSE,
                    citation_path=citation_path,
                    page_start=page,
                    page_end=page,
                    text=(
                        "The maximum building height and the minimum front "
                        "yard setback in this district are governed by "
                        f"clause {citation_path}."
                    ),
                    parse_status=ParseStatus.PARSED,
                    confidence=1.0,
                    attribute_tags=tags,
                )
            )

    session_cm = session_scope(db_url)
    session = session_cm.__enter__()
    yield RetrievalService(session)
    session_cm.__exit__(None, None, None)


@pytest.mark.asyncio
async def test_handler_narrows_results_via_attribute_tag_filter(tagged_service):
    """A chat tool call carrying ``attribute_tag_filter`` narrows the set.

    Unfiltered, the query matches all three clauses (they share text).
    Filtered on ``building_height_m``, only the height clause survives —
    proving the argument reached ``RetrievalRequest`` and the indexed
    ``attribute_tags`` clause, not that it was dropped on the floor.
    """
    _, handlers = build_bylaw_tools(tagged_service)

    unfiltered = json.loads(
        await handlers["search_bylaw_evidence"](
            {"query": "maximum building height setback", "limit": 10}
        )
    )
    unfiltered_paths = {m["citation_path"] for m in unfiltered["matches"]}
    assert unfiltered_paths == {"4.2.1", "4.3.1", "4.4.1"}, (
        "fixture precondition: the bare text query must match every clause, "
        "otherwise the narrowing assertion below proves nothing"
    )

    filtered = json.loads(
        await handlers["search_bylaw_evidence"](
            {
                "query": "maximum building height setback",
                "attribute_tag_filter": ["building_height_m"],
                "limit": 10,
            }
        )
    )
    assert {m["citation_path"] for m in filtered["matches"]} == {"4.3.1"}


@pytest.mark.asyncio
async def test_handler_unions_multiple_attribute_tags(tagged_service):
    """Multiple IDs are any-of, not all-of — the semantic the schema promises."""
    _, handlers = build_bylaw_tools(tagged_service)

    filtered = json.loads(
        await handlers["search_bylaw_evidence"](
            {
                "query": "maximum building height setback",
                "attribute_tag_filter": ["building_height_m", "lot_coverage_percent"],
                "limit": 10,
            }
        )
    )
    assert {m["citation_path"] for m in filtered["matches"]} == {"4.3.1", "4.4.1"}


@pytest.mark.asyncio
async def test_empty_attribute_tag_filter_is_rejected_not_silently_ignored(
    tagged_service,
):
    """``[]`` must raise, not fall through the service's truthiness gate.

    ``RetrievalService.search`` gates on ``if request.attribute_tag_filter:``,
    so an empty list would silently mean "no filter" — the opposite of
    ``_attribute_tag_filter_clause``'s rule, which rejects empty input
    because an empty clause set degrades into always-true/always-false.
    """
    _, handlers = build_bylaw_tools(tagged_service)

    with pytest.raises(ValidationError, match="attribute_tag_filter must be non-empty"):
        await handlers["search_bylaw_evidence"](
            {"query": "maximum building height", "attribute_tag_filter": []}
        )


@pytest.mark.asyncio
async def test_empty_filter_surfaces_as_tool_error_not_a_crash(tagged_service):
    """The rejection reaches the model as an is_error tool_result.

    This is the half of the contract that matters in production: the tool
    loop must convert the validation failure into something the model can
    read and correct, not let it escape as an unhandled 500.
    """
    _, handlers = build_bylaw_tools(tagged_service)

    invocation = await _run_one_handler(
        handlers,
        ToolUseBlock(
            id="toolu_empty_filter",
            name="search_bylaw_evidence",
            input={"query": "maximum building height", "attribute_tag_filter": []},
        ),
    )

    assert invocation.output is None
    assert invocation.error is not None
    assert "attribute_tag_filter must be non-empty" in invocation.error


def test_chat_and_openai_specs_agree_on_search_bylaw_evidence_properties():
    """Spec parity: neither surface may carry a parameter the other lacks.

    ABS-466 updated one surface and not the other; ABS-469 (``fe705e3``) had
    to go back and re-sync it. This pins the invariant instead of relying on
    a reviewer noticing.
    """
    tool_defs, _ = build_bylaw_tools(lambda: None)
    chat_schema = next(
        t for t in tool_defs if t.name == "search_bylaw_evidence"
    ).input_schema
    openai_schema = next(
        spec
        for spec in build_openai_responses_tool_specs()
        if spec["name"] == "search_bylaw_evidence"
    )["parameters"]

    assert set(chat_schema["properties"]) == set(openai_schema["properties"])
    assert chat_schema["required"] == openai_schema["required"]
    # The shared property must be byte-identical, not merely present: a
    # divergent description is exactly the ABS-469 failure mode.
    assert (
        chat_schema["properties"]["attribute_tag_filter"]
        == openai_schema["properties"]["attribute_tag_filter"]
        == ATTRIBUTE_TAG_FILTER_PROPERTY
    )


def test_mcp_server_signature_accepts_every_advertised_parameter():
    """The MCP tool is the third surface — its signature must cover the schema.

    ``mcp/bylaw_retrieval/server.py`` declares the tool as a plain Python
    signature rather than a JSON Schema, so parity there means "every
    advertised property is an accepted keyword". A property the MCP tool
    can't accept would be a TypeError at call time.

    The import is ``bylaw_retrieval``, NOT ``mcp.bylaw_retrieval``: the repo's
    ``mcp/`` directory is itself on ``pythonpath`` (pyproject), and the name
    ``mcp`` belongs to the installed MCP SDK. Importing through the ``mcp.``
    prefix made this test's outcome depend on whether that unrelated SDK
    happened to be installed (ABS-503).
    """
    from bylaw_retrieval import server as mcp_server  # noqa: PLC0415

    # Guard against the collision silently returning: if ``bylaw_retrieval``
    # ever resolved to something under site-packages, the parity assertions
    # below would be checking the wrong source and could pass for the wrong
    # reason.
    assert Path(mcp_server.__file__).resolve() == REPO_MCP_SERVER_PATH, (
        f"expected the repo's MCP server module at {REPO_MCP_SERVER_PATH}, "
        f"got {mcp_server.__file__}"
    )

    source = inspect.getsource(mcp_server.create_mcp_server)
    signature_start = source.index("def search_bylaw_evidence(")
    signature = source[signature_start : source.index(") -> dict:", signature_start)]

    openai_schema = next(
        spec
        for spec in build_openai_responses_tool_specs()
        if spec["name"] == "search_bylaw_evidence"
    )["parameters"]
    for name in openai_schema["properties"]:
        assert f"{name}:" in signature, (
            f"MCP search_bylaw_evidence does not accept {name!r}, but the "
            "OpenAI/chat specs advertise it"
        )


def test_attribute_tag_filter_description_lists_only_real_taxonomy_ids():
    """Every ID named in the tool description must exist in taxonomy.yaml.

    The description is the model's only source for valid IDs, and an ID
    outside the taxonomy matches no clause — a hallucinated example in the
    prompt would produce confidently empty searches.
    """
    taxonomy = yaml.safe_load(TAXONOMY_PATH.read_text())
    known = {entry["id"] for entry in taxonomy["attributes"]}

    description = ATTRIBUTE_TAG_FILTER_PROPERTY["description"]
    assert "taxonomy.yaml" in description, (
        "the issue requires the description to point at the taxonomy file"
    )
    named = {word.strip(" ,.'[]") for word in description.split()}
    cited = {token for token in named if token.endswith(("_m", "_m2", "_count", "_percent", "_code", "_ratio", "_boolean", "_class", "_classes", "_type", "_mix", "_catchment", "_overlay", "_storeys"))}
    assert cited, "description should name concrete attribute IDs as examples"
    assert cited <= known, f"description names non-taxonomy IDs: {sorted(cited - known)}"
