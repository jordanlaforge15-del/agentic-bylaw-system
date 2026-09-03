// ABS-522 — the `claude_code` provider is gone, and a deployment still
// asking for it fails loudly.
//
// This change is server-only: no route, component or user-visible surface
// moves. What it does move is the thing that decides whether the advisor
// boots at all, and how its turns are billed. `build_gateway()`
// (src/advisor/llm/registry.py) runs once per deployment, against the
// process environment the container actually inherits.
//
// tests/advisor/llm/test_registry.py pins the resolution logic with a
// hand-built AdvisorLLMSettings, which is the right shape for the branch
// and structurally blind to the two things that decide a real boot:
//
//  (a) WHETHER `ADVISOR_LLM_PROVIDER` IS READ AT ALL. A settings field
//      renamed out from under its alias, a typo in the alias, or a stray
//      `.env` shadowing the process env all keep the unit suite green —
//      it never asks the environment anything. Here the value travels as
//      a real env var into a real interpreter.
//
//  (b) WHETHER A STALE `claude_code` IS REJECTED RATHER THAN COERCED.
//      This is the money assertion. Operators ran that backend precisely
//      to keep turns on a Claude Code subscription instead of metered API
//      billing. Removing the branch and letting the value fall through to
//      AnthropicGateway would silently start charging them — the same
//      class of accident that ran ~$1.70 through the wrong provider
//      during this work. It must raise.
//
// The probe also asserts the removal is real at the filesystem level: the
// CLI backend and translation modules must not be importable from the
// installed package. Unreferenced is not removed.
//
// Driven through POST /v1/_test/llm-registry-probe
// (src/advisor/api/e2e_server.py), which spawns one interpreter with an
// env assembled from scratch, calls build_gateway(), and reports what it
// got or how it died. No `claude` binary is invoked and none needs to
// exist in the e2e container.

import { expect, test } from "@playwright/test";

const E2E_API_URL = process.env.E2E_API_URL ?? "http://127.0.0.1:8001";
const PROBE = `${E2E_API_URL}/v1/_test/llm-registry-probe`;

const FAKE_KEY = "sk-ant-abs522-e2e-not-a-real-key";

type ProbeResult = {
  returncode: number;
  stderr_tail: string;
  ok: boolean;
  gateway_name?: string;
  gateway_class?: string;
  error_type?: string;
  error?: string;
  cli_backend_importable: boolean;
  cli_translation_importable: boolean;
};

async function probe(
  request: import("@playwright/test").APIRequestContext,
  provider: string | null,
  env: Record<string, string> = {},
): Promise<ProbeResult> {
  const response = await request.post(PROBE, { data: { provider, env } });
  expect(
    response.status(),
    `registry probe failed: ${await response.text()}`,
  ).toBe(200);
  return (await response.json()) as ProbeResult;
}

// Each probe spawns an interpreter that imports the advisor package —
// ~0.4s warm, slower on the first one while the OS page cache fills.
test.describe.configure({ timeout: 60_000 });

test.describe("ABS-522 single-provider LLM registry", () => {
  test("claude_code no longer resolves to a gateway", async ({ request }) => {
    const result = await probe(request, "claude_code", {
      // The key is present on purpose. Under the old registry this
      // combination was refused outright (the CLI would have billed API
      // rates). Under the new one the provider itself is the failure, and
      // it must not matter whether a key happens to be around — a build
      // that succeeded here would be one silently billing the operator.
      ANTHROPIC_API_KEY: FAKE_KEY,
    });

    expect(result.ok, `expected a failure, got ${result.gateway_class}`).toBe(
      false,
    );
    expect(result.error_type).toBe("ValueError");
    expect(result.error).toContain("claude_code");
    expect(result.error).toContain("ABS-522");
    expect(result.gateway_name).toBeUndefined();
  });

  test("claude_code fails on the provider, not on a missing key", async ({
    request,
  }) => {
    // A real claude_code deployment has no ANTHROPIC_API_KEY — that was
    // the entire point of the backend. If the key check ran first, every
    // such deployment would boot-fail with "ANTHROPIC_API_KEY is
    // required" and the operator would go add a key, which is both the
    // wrong fix and the expensive one.
    const result = await probe(request, "claude_code");

    expect(result.ok).toBe(false);
    expect(result.error_type).toBe("ValueError");
    expect(result.error).toContain("claude_code");
    expect(result.error).not.toContain("ANTHROPIC_API_KEY is required");
  });

  test("the removed modules are not importable from the package", async ({
    request,
  }) => {
    const result = await probe(request, "anthropic", {
      ANTHROPIC_API_KEY: FAKE_KEY,
    });

    expect(result.cli_backend_importable).toBe(false);
    expect(result.cli_translation_importable).toBe(false);
  });

  test("anthropic still boots on a key and still fails without one", async ({
    request,
  }) => {
    const withKey = await probe(request, "anthropic", {
      ANTHROPIC_API_KEY: FAKE_KEY,
    });
    expect(withKey.ok, withKey.error).toBe(true);
    expect(withKey.gateway_name).toBe("anthropic");
    expect(withKey.gateway_class).toBe("AnthropicGateway");

    const withoutKey = await probe(request, "anthropic");
    expect(withoutKey.ok).toBe(false);
    expect(withoutKey.error_type).toBe("RuntimeError");
    expect(withoutKey.error).toContain("ANTHROPIC_API_KEY is required");
  });

  test("an unset provider defaults to anthropic", async ({ request }) => {
    // Production's compose file passes ADVISOR_LLM_PROVIDER through with
    // an `anthropic` default, but a container started without it must
    // land in the same place rather than on an "unsupported ''" error.
    const result = await probe(request, null, { ANTHROPIC_API_KEY: FAKE_KEY });

    expect(result.ok, result.error).toBe(true);
    expect(result.gateway_name).toBe("anthropic");
  });

  test("an unknown provider raises and names the supported one", async ({
    request,
  }) => {
    const result = await probe(request, "openai", {
      ANTHROPIC_API_KEY: FAKE_KEY,
    });

    expect(result.ok).toBe(false);
    expect(result.error_type).toBe("ValueError");
    expect(result.error).toContain("openai");
    expect(result.error).toContain("anthropic");
  });
});
