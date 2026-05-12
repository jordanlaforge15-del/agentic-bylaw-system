"""End-to-end verification that prompt caching is actually wired up.

Why this exists: unit tests can only confirm we *emit* ``cache_control``
markers. They can't confirm Anthropic accepts them and serves cache
reads on a subsequent call. That round-trip is the entire reason this
workstream exists, so we have a one-off script that drives it against
the real API.

What it does:
1. Builds a ``CompletionRequest`` shaped like the chat session's
   (persona-as-system, the four bylaw tools, ``cache_system`` /
   ``cache_tools`` flipped on).
2. Sends a tool-use-prone first turn. If the model returns
   ``tool_use``, builds a second turn with a stub tool_result and
   sends that. If the model decides to answer directly without tools,
   falls back to sending the same request again — the test for
   "cache_read on iteration 2" still holds because the prefix is
   identical.
3. Prints per-iteration ``TokenUsage`` and asserts
   ``cache_read_input_tokens > 0`` on the second iteration.

Run:
    ANTHROPIC_API_KEY=sk-... python scripts/verify_prompt_cache.py

Exits 0 on success, 1 on failure or missing key.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Make ``src/`` importable when running from the repo root without
# install — this is a verification script, not a packaged entry point.
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "src"))

from advisor.chat.persona import load_persona  # noqa: E402
from advisor.chat.tools import build_bylaw_tools  # noqa: E402
from advisor.llm import (  # noqa: E402
    CompletionRequest,
    LLMRole,
    Message,
    ToolResultBlock,
    ToolUseBlock,
)
from advisor.llm.anthropic_backend import AnthropicGateway  # noqa: E402

_MODEL = os.environ.get("ADVISOR_LLM_MODEL", "claude-opus-4-5")
_PROBE_QUESTION = (
    "What is the maximum building height permitted at 6321 Quinpool "
    "Road in Halifax? Search the bylaw for the answer and cite the "
    "specific schedule."
)


async def _run() -> int:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ANTHROPIC_API_KEY is not set; cannot verify against live API.")
        return 1

    persona = load_persona()
    # We don't actually want to hit a real retrieval service — we just
    # need the tool *definitions* to be byte-identical to production
    # so the cached prefix matches what the deployed advisor caches.
    # The handlers are never invoked: the second turn synthesises a
    # tool_result ourselves to keep this script DB-free.
    tool_defs, _ = build_bylaw_tools(retrieval_service=lambda: _RaiseService())

    gateway = AnthropicGateway(api_key=api_key)

    iter1_msgs = [Message(role=LLMRole.USER, content=_PROBE_QUESTION)]
    req1 = CompletionRequest(
        model=_MODEL,
        system=persona,
        messages=iter1_msgs,
        tools=tool_defs,
        cache_system=True,
        cache_tools=True,
        max_tokens=1024,
    )

    print("→ Iteration 1: priming the cache...")
    resp1 = await gateway.complete(req1)
    _print_usage("iter 1", resp1.usage)

    # Build iteration 2 — prefer a real tool-use round-trip (matches
    # the chat backend's hottest code path) but degrade to a plain
    # re-send if the model answered directly. Either way the second
    # request has the same cached prefix and should show cache reads.
    tool_use = next(
        (b for b in resp1.content if isinstance(b, ToolUseBlock)), None
    )
    if tool_use is not None:
        print(f"  model requested tool {tool_use.name!r}; building tool_result.")
        iter2_msgs = [
            *iter1_msgs,
            Message(role=LLMRole.ASSISTANT, content=list(resp1.content)),
            Message(
                role=LLMRole.USER,
                content=[
                    ToolResultBlock(
                        tool_use_id=tool_use.id,
                        content='{"matches": [], "note": "stub for cache verification"}',
                    )
                ],
            ),
        ]
    else:
        print("  model answered without tools; resending the same prefix.")
        iter2_msgs = iter1_msgs

    req2 = req1.model_copy(update={"messages": iter2_msgs})

    print("→ Iteration 2: should read cache...")
    resp2 = await gateway.complete(req2)
    _print_usage("iter 2", resp2.usage)

    if resp2.usage is None:
        print("FAIL: iteration 2 returned no usage; cannot verify cache.")
        return 1
    if resp2.usage.cache_read_input_tokens <= 0:
        print(
            "FAIL: expected cache_read_input_tokens > 0 on iteration 2, "
            f"got {resp2.usage.cache_read_input_tokens}."
        )
        return 1

    print(
        f"\nOK: prompt cache is working — iteration 2 read "
        f"{resp2.usage.cache_read_input_tokens} tokens from cache."
    )
    return 0


def _print_usage(label: str, usage) -> None:  # noqa: ANN001 — usage is TokenUsage | None
    if usage is None:
        print(f"  {label}: usage=None")
        return
    print(
        f"  {label}: input={usage.input_tokens}, output={usage.output_tokens}, "
        f"cache_creation={usage.cache_creation_input_tokens}, "
        f"cache_read={usage.cache_read_input_tokens}"
    )


class _RaiseService:
    """Stand-in for a RetrievalService. Tool handlers are never called
    in this script (we synthesise the tool_result inline), but
    ``build_bylaw_tools`` needs *something* shaped like a service
    factory. Touching any method raises so a future change that
    accidentally exercises a handler fails loudly here rather than
    silently calling Postgres."""

    def __getattr__(self, name: str):
        raise AssertionError(
            f"_RaiseService.{name} should not be called in this script; "
            "handlers are stubbed via inline tool_result."
        )


if __name__ == "__main__":
    sys.exit(asyncio.run(_run()))
