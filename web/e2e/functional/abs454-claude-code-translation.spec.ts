// ABS-454 — `claude -p` request/response translation layer.
//
// The layer under test (src/advisor/llm/claude_code_translation.py) is
// pure: prompt in, envelope out, no process spawning. Its contract is
// pinned function-by-function by tests/advisor/llm/
// test_claude_code_translation.py. This spec exists for the two things
// those unit tests structurally cannot see, both of which only show up
// once the module is loaded inside a running advisor process:
//
//  (a) IMPORTABILITY IN A DEPLOYED IMAGE. The module imports
//      `jsonschema`, which before this issue was a [dev]-only
//      dependency. Prod installs `.[advisor]`, so a unit suite running
//      in a dev venv would stay green while the deployed gateway threw
//      ImportError on the first request. Every assertion below travels
//      through the FastAPI app, so a missing extra is a red spec.
//
//  (b) AGREEMENT WITH THE REAL TOOL MENU. The endpoints build the
//      envelope schema and validate tool inputs against the tools
//      `advisor.chat.tools.build_bylaw_tools` actually ships, not
//      against fixtures. Renaming a bylaw tool, or tightening its
//      input_schema, without re-deriving the envelope surfaces here.
//
// Driven through /v1/_test/claude-code-translation{,/schema}
// (src/advisor/api/e2e_server.py). Rejections come back 200 with
// `ok: false` and a typed `error_type` — that type is the contract the
// ABS-455 transport dispatches retries on, so the spec reads it rather
// than an HTTP status.

import { expect, test } from "@playwright/test";

const E2E_API_URL = process.env.E2E_API_URL ?? "http://127.0.0.1:8001";
const TRANSLATE = `${E2E_API_URL}/v1/_test/claude-code-translation`;
const SCHEMA = `${TRANSLATE}/schema`;

type ContentBlock = {
  type: string;
  text?: string;
  id?: string;
  name?: string;
  input?: Record<string, unknown>;
};

type TranslateResult = {
  ok: boolean;
  id?: string | null;
  stop_reason?: string;
  content?: ContentBlock[];
  usage?: Record<string, number> | null;
  error_type?: string;
  message?: string;
};

async function translate(
  request: import("@playwright/test").APIRequestContext,
  body: Record<string, unknown>,
): Promise<TranslateResult> {
  const response = await request.post(TRANSLATE, { data: body });
  expect(
    response.status(),
    `translation endpoint failed: ${await response.text()}`,
  ).toBe(200);
  return (await response.json()) as TranslateResult;
}

test.describe("claude -p translation layer (ABS-454)", () => {
  test("envelope schema is built from the live bylaw tool menu", async ({
    request,
  }) => {
    const response = await request.get(SCHEMA);
    expect(
      response.status(),
      `schema endpoint failed — a missing [advisor] extra (jsonschema) or a ` +
        `broken import in advisor.llm.claude_code_translation looks exactly ` +
        `like this: ${await response.text()}`,
    ).toBe(200);
    const body = (await response.json()) as {
      tool_names: string[];
      schema: Record<string, any>;
      prompt: string;
    };

    expect(body.tool_names).toContain("search_bylaw_evidence");

    // tool_calls must stay an ARRAY. The Messages API emits parallel
    // tool_use blocks and tool_loop.py iterates them; collapsing the
    // envelope to a single object would silently serialise work the
    // model asked for at once.
    expect(
      body.schema.properties.tool_calls.type,
      "ABS-454: tool_calls collapsed from a list — parallel tool calls " +
        "would be silently dropped in tool_loop.py",
    ).toBe("array");
    expect(body.schema.properties.action.enum).toEqual([
      "tool_calls",
      "final_answer",
    ]);

    // The name enum is the hallucination guard, and it has to track the
    // real menu — not a hard-coded list that drifts.
    const nameEnum = body.schema.properties.tool_calls.items.properties.name
      .enum as string[];
    expect([...nameEnum].sort()).toEqual([...body.tool_names].sort());
  });

  test("rendered prompt carries system text, every tool, and prior tool turns", async ({
    request,
  }) => {
    const first = await request.get(SCHEMA);
    expect(first.status()).toBe(200);
    const body = (await first.json()) as {
      tool_names: string[];
      prompt: string;
    };

    expect(body.prompt).toContain("ABS-454 system persona under test.");
    for (const name of body.tool_names) {
      expect(body.prompt, `tool ${name} missing from the rendered menu`).toContain(
        `## ${name}`,
      );
    }
    // Prior tool_use / tool_result turns have to survive the flattening,
    // otherwise the model loses the results it already paid for.
    expect(body.prompt).toContain("[tool_call id=toolu_abs454]");
    expect(body.prompt).toContain("[tool_result id=toolu_abs454 status=ok]");

    // Byte-identical across calls: the transport caches on the prompt
    // and prompt caching only pays off on a stable prefix.
    const second = await request.get(SCHEMA);
    const secondBody = (await second.json()) as { prompt: string };
    expect(secondBody.prompt).toBe(body.prompt);
  });

  test("final_answer yields end_turn even when the CLI payload says tool_use", async ({
    request,
  }) => {
    // The probe-critical rule (2026-08-09): --json-schema is a forced
    // tool call under the hood, so the CLI reports stop_reason
    // "tool_use" for a semantically final answer. Passing that through
    // makes tool_loop wait forever for calls that never come.
    const result = await translate(request, {
      structured_output: {
        action: "final_answer",
        text: "The maximum height in R-1 is 10 m.",
      },
      payload: {
        stop_reason: "tool_use",
        uuid: "abs454-final",
        session_id: "abs454-session",
        usage: {
          input_tokens: 1200,
          output_tokens: 34,
          cache_creation_input_tokens: 900,
          cache_read_input_tokens: 7,
        },
      },
    });

    expect(result.ok).toBe(true);
    expect(
      result.stop_reason,
      "ABS-454 regression: stop_reason was read from the CLI payload " +
        "instead of derived from the envelope's action. tool_loop will " +
        "hang waiting for tool calls a final_answer never contains.",
    ).toBe("end_turn");
    expect(result.content).toHaveLength(1);
    expect(result.content?.[0].type).toBe("text");
    expect(result.content?.[0].text).toBe("The maximum height in R-1 is 10 m.");
    expect(result.id).toBe("abs454-final");
    // Usage maps 1:1 onto TokenUsage — cost attribution depends on it.
    expect(result.usage).toEqual({
      input_tokens: 1200,
      output_tokens: 34,
      cache_creation_input_tokens: 900,
      cache_read_input_tokens: 7,
    });
  });

  test("parallel tool_calls become distinct tool_use blocks with unique ids", async ({
    request,
  }) => {
    const result = await translate(request, {
      structured_output: {
        action: "tool_calls",
        text: "Pulling both sources.",
        tool_calls: [
          { name: "search_bylaw_evidence", input: { query: "R-1 height" } },
          { name: "get_zone_profile", input: { zone: "R-1" } },
        ],
      },
      payload: { uuid: "abs454-parallel", session_id: "abs454-session" },
    });

    expect(result.ok).toBe(true);
    expect(result.stop_reason).toBe("tool_use");
    // Optional preamble text block first, then one tool_use per call.
    expect(result.content?.[0].type).toBe("text");
    const toolUses = (result.content ?? []).filter((b) => b.type === "tool_use");
    expect(toolUses).toHaveLength(2);
    expect(toolUses.map((b) => b.name)).toEqual([
      "search_bylaw_evidence",
      "get_zone_profile",
    ]);
    // Ids correlate tool_use ↔ tool_result; duplicates would cross-wire
    // results between the two calls.
    expect(new Set(toolUses.map((b) => b.id)).size).toBe(2);
    expect(toolUses[0].input).toEqual({ query: "R-1 height" });
    // No usage reported ≠ free: absent usage stays null.
    expect(result.usage).toBeNull();
  });

  test("bad envelopes are rejected with the typed error the transport retries on", async ({
    request,
  }) => {
    const hallucinated = await translate(request, {
      structured_output: {
        action: "tool_calls",
        tool_calls: [{ name: "definitely_not_a_bylaw_tool", input: {} }],
      },
      payload: null,
    });
    expect(hallucinated.ok).toBe(false);
    expect(hallucinated.error_type).toBe("UnknownToolError");
    // The message names the real menu, so a live failure is debuggable.
    expect(hallucinated.message).toContain("search_bylaw_evidence");

    // Input validated against the live tool's input_schema.
    const badInput = await translate(request, {
      structured_output: {
        action: "tool_calls",
        tool_calls: [{ name: "search_bylaw_evidence", input: { query: 123 } }],
      },
      payload: null,
    });
    expect(badInput.ok).toBe(false);
    expect(badInput.error_type).toBe("ToolInputValidationError");

    // An empty tool_calls list and a blank final_answer are the same
    // bug wearing different hats: the loop terminates and the user gets
    // nothing. Both must raise so the transport can retry the turn.
    const emptyCalls = await translate(request, {
      structured_output: { action: "tool_calls", tool_calls: [] },
      payload: null,
    });
    expect(emptyCalls.ok).toBe(false);
    expect(emptyCalls.error_type).toBe("EnvelopeValidationError");

    const blankAnswer = await translate(request, {
      structured_output: { action: "final_answer", text: "   " },
      payload: null,
    });
    expect(blankAnswer.ok).toBe(false);
    expect(blankAnswer.error_type).toBe("EnvelopeValidationError");

    const unknownAction = await translate(request, {
      structured_output: { action: "give_up" },
      payload: null,
    });
    expect(unknownAction.ok).toBe(false);
    expect(unknownAction.error_type).toBe("EnvelopeValidationError");
  });
});
