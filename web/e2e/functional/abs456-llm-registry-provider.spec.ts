// ABS-456 — `claude_code` in the gateway registry, behind the API-key
// billing guard.
//
// `build_gateway()` (src/advisor/llm/registry.py) runs once per deployment:
// at boot, against the process environment the service actually inherits.
// tests/advisor/llm/test_registry.py pins the branch logic with a hand-built
// AdvisorLLMSettings and a monkeypatched os.environ, which is the right shape
// for the dispatch table but structurally blind to the two things that decide
// whether a deployment boots at all:
//
//  (a) WHETHER THE ENV VARS ARE READ. A settings field renamed out from under
//      its `ADVISOR_*` alias, a typo in an alias, or a stray `.env` shadowing
//      the process env all keep the unit suite green — it never asks the
//      environment anything. Here the knobs travel as real env vars into a
//      real interpreter.
//
//  (b) WHETHER THE GUARD FIRES ON A REAL os.environ. The one failure this
//      whole backend exists to prevent is a boot that silently meters:
//      `claude -p` with ANTHROPIC_API_KEY present bills API rates
//      (anthropics/claude-code#43333). A monkeypatched dict proves the `if`;
//      only a spawned process proves the boot.
//
// Driven through POST /v1/_test/llm-registry-probe (src/advisor/api/e2e_server.py),
// which spawns one interpreter with an env assembled from scratch, calls
// build_gateway(), and reports what it got or how it died. No `claude` binary
// is invoked and none needs to exist in the e2e container.

import { expect, test } from "@playwright/test";

const E2E_API_URL = process.env.E2E_API_URL ?? "http://127.0.0.1:8001";
const PROBE = `${E2E_API_URL}/v1/_test/llm-registry-probe`;

type ProbeResult = {
  returncode: number;
  stderr_tail: string;
  ok: boolean;
  gateway_name?: string;
  gateway_class?: string;
  cli_path?: string | null;
  timeout_s?: number;
  max_retries?: number;
  error_type?: string;
  error?: string;
  cli_backend_imported_on_registry_import?: boolean;
  cli_backend_imported_after_build?: boolean;
};

async function probe(
  request: import("@playwright/test").APIRequestContext,
  provider: string,
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

test.describe("ABS-456 LLM provider registry", () => {
  test("claude_code selects the CLI gateway when no API key is in the env", async ({
    request,
  }) => {
    const result = await probe(request, "claude_code");

    expect(result.error, result.stderr_tail).toBeUndefined();
    expect(result.ok).toBe(true);
    expect(result.gateway_name).toBe("claude_code");
    expect(result.gateway_class).toBe("ClaudeCodeGateway");
  });

  test("claude_code refuses to boot with ANTHROPIC_API_KEY in the environment", async ({
    request,
  }) => {
    const result = await probe(request, "claude_code", {
      ANTHROPIC_API_KEY: "sk-ant-abs456-e2e-not-a-real-key",
    });

    expect(result.ok).toBe(false);
    expect(result.error_type).toBe("RuntimeError");
    expect(result.error).toContain("ANTHROPIC_API_KEY");
    expect(result.error).toContain("refusing to start");
    // The guard runs *before* construction: a broken CLI install must not be
    // able to mask the billing failure with a different exception.
    expect(result.gateway_name).toBeUndefined();
  });

  test("the ADVISOR_CLAUDE_CODE_* knobs reach the constructed gateway", async ({
    request,
  }) => {
    const configured = await probe(request, "claude_code", {
      ADVISOR_CLAUDE_CODE_CLI_PATH: "/opt/claude/bin/claude",
      ADVISOR_CLAUDE_CODE_TIMEOUT_S: "45",
      ADVISOR_CLAUDE_CODE_MAX_RETRIES: "1",
    });

    expect(configured.ok, configured.error).toBe(true);
    expect(configured.cli_path).toBe("/opt/claude/bin/claude");
    expect(configured.timeout_s).toBe(45);
    expect(configured.max_retries).toBe(1);

    // Unset means the documented defaults, not None — an operator who sets
    // nothing still gets a bounded turn and a bounded retry count.
    const defaults = await probe(request, "claude_code");
    expect(defaults.timeout_s).toBe(300);
    expect(defaults.max_retries).toBe(3);
  });

  test("anthropic still boots on a key and still fails without one", async ({
    request,
  }) => {
    const withKey = await probe(request, "anthropic", {
      ANTHROPIC_API_KEY: "sk-ant-abs456-e2e-not-a-real-key",
    });
    expect(withKey.ok, withKey.error).toBe(true);
    expect(withKey.gateway_name).toBe("anthropic");
    // Production's path must not drag the CLI backend in — a boot that never
    // takes the claude_code branch must not be breakable by that module.
    expect(withKey.cli_backend_imported_on_registry_import).toBe(false);
    expect(withKey.cli_backend_imported_after_build).toBe(false);

    const withoutKey = await probe(request, "anthropic");
    expect(withoutKey.ok).toBe(false);
    expect(withoutKey.error_type).toBe("RuntimeError");
    expect(withoutKey.error).toContain("ANTHROPIC_API_KEY is required");
  });

  test("an unknown provider names both supported values", async ({
    request,
  }) => {
    const result = await probe(request, "openai");

    expect(result.ok).toBe(false);
    expect(result.error_type).toBe("ValueError");
    expect(result.error).toContain("openai");
    expect(result.error).toContain("anthropic");
    expect(result.error).toContain("claude_code");
  });
});
