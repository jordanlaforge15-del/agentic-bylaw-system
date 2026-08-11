// ABS-455 — `claude -p` transport + gateway class.
//
// The transport (src/advisor/llm/claude_code_backend.py) is pinned
// attempt-by-attempt by tests/advisor/llm/test_claude_code_backend.py,
// where every subprocess is faked. This spec covers what those tests
// structurally cannot, and deliberately still spawns nothing:
//
//  (a) IMPORTABILITY IN A DEPLOYED IMAGE. The transport pulls in the
//      ABS-454 translation layer and therefore `jsonschema`, an extra
//      that lived under [dev] until that issue. A unit suite in a dev
//      venv stays green while the deployed gateway throws ImportError
//      on the first chat turn. The endpoint is mounted at app startup,
//      so a missing extra is a red spec here.
//
//  (b) THE TWO LOAD-BEARING FLAGS, READ OFF A LIVE GATEWAY BUILT FROM
//      THE REAL TOOL MENU. `--disallowedTools` is what demotes the CLI
//      from an agent to a completion engine, leaving the tool loop —
//      and with it the per-turn charge and the cost breakers — on our
//      side. `--autocompact 1000000` pins compaction far above the
//      advisor's own ~165k cumulative-token breaker so ours trips
//      first and the CLI never rewrites history we bill and cite from.
//      Losing either is a silent, expensive regression.
//
// Driven through GET /v1/_test/claude-code-transport
// (src/advisor/api/e2e_server.py), which asks the gateway what command
// line it *would* run. No `claude` binary is invoked, and none needs to
// exist in the e2e container.

import { expect, test } from "@playwright/test";

const E2E_API_URL = process.env.E2E_API_URL ?? "http://127.0.0.1:8001";
const TRANSPORT = `${E2E_API_URL}/v1/_test/claude-code-transport`;

type TransportResult = {
  name: string;
  is_llm_gateway: boolean;
  argv: string[];
  system: string;
  tool_names: string[];
  disallowed_tools: string[];
  autocompact_threshold: number;
};

async function fetchTransport(
  request: import("@playwright/test").APIRequestContext,
): Promise<TransportResult> {
  const response = await request.get(TRANSPORT);
  expect(
    response.status(),
    `transport endpoint failed: ${await response.text()}`,
  ).toBe(200);
  return (await response.json()) as TransportResult;
}

/** Value of `--flag` in an argv list, or undefined when absent. */
function flag(argv: string[], name: string): string | undefined {
  const index = argv.indexOf(name);
  return index === -1 ? undefined : argv[index + 1];
}

test.describe("ABS-455 claude -p transport", () => {
  test("gateway loads in the running advisor and satisfies the protocol", async ({
    request,
  }) => {
    const result = await fetchTransport(request);

    expect(result.name).toBe("claude_code");
    // Bare class, no base — conformance is structural, so a drifted
    // signature shows up as a false isinstance rather than an import
    // error, and only a live check catches it.
    expect(result.is_llm_gateway).toBe(true);
  });

  test("argv keeps the agentic loop and the compaction threshold on our side", async ({
    request,
  }) => {
    const { argv, disallowed_tools, autocompact_threshold } =
      await fetchTransport(request);

    expect(argv[1]).toBe("-p");
    expect(flag(argv, "--output-format")).toBe("json");
    expect(flag(argv, "--model")).toBe("claude-code-e2e");

    const disallowed = flag(argv, "--disallowedTools") ?? "";
    expect(disallowed.split(/[,\s]+/).sort()).toEqual(
      [...disallowed_tools].sort(),
    );
    // Spot-check the ones that would actually let a turn act on the
    // world if they ever fell off the list.
    for (const builtin of ["Bash", "Read", "Write", "Edit", "WebSearch", "Task"]) {
      expect(disallowed).toContain(builtin);
    }

    expect(flag(argv, "--autocompact")).toBe("1000000");
    expect(autocompact_threshold).toBeGreaterThan(165_000);
  });

  test("envelope schema is built from the tools the advisor actually ships", async ({
    request,
  }) => {
    const { argv, tool_names } = await fetchTransport(request);

    const schema = JSON.parse(flag(argv, "--json-schema") ?? "{}");
    expect(schema.properties.action.enum.sort()).toEqual([
      "final_answer",
      "tool_calls",
    ]);
    // The name enum tracks the live menu: add or rename a bylaw tool
    // without re-deriving the envelope and this goes red.
    expect(tool_names.length).toBeGreaterThan(0);
    expect(schema.properties.tool_calls.items.properties.name.enum).toEqual(
      tool_names,
    );
  });

  test("system prompt is sent once, on the CLI's own system slot", async ({
    request,
  }) => {
    const { argv, system, tool_names } = await fetchTransport(request);

    expect(flag(argv, "--append-system-prompt")).toBe(system);
    // Not duplicated into the prompt body — advisor personas are large
    // enough that sending one twice is a measurable per-turn cost.
    const prompt = flag(argv, "-p") ?? "";
    expect(prompt).not.toContain(system);
    // The tool menu and protocol rules do still ride in the body.
    expect(prompt).toContain(tool_names[0]);
    expect(prompt).toContain("final_answer");
  });
});
