// ABS-531 — the installed Anthropic SDK must accept the kwargs the gateway
// sends on every single request.
//
// WHAT BROKE. Prod threw on every case-open:
//
//     AsyncMessages.create() got an unexpected keyword argument 'temperature'
//
// `pyproject.toml` declared `anthropic>=0.40` — a floor, and nothing in the
// repo locked it. The dev venv installed 0.100.0 in May and pip never revisited
// it (0.100.0 already satisfies the floor); Dockerfile.advisor builds into an
// EMPTY venv on every image build and resolved 1.3.0. anthropic 1.x removed the
// sampling parameters from the Messages API, so the `temperature` that
// AnthropicGateway._to_anthropic_params has always sent became a TypeError.
// Both request paths were dead — complete() and stream() share that dict.
//
// WHY THE WHOLE SUITE STAYED GREEN. Two independent blind spots, and the bug
// walked between them:
//
//  (a) The unit tests drive `_to_anthropic_params` and assert on the dict it
//      builds. They never ask whether the SDK being shipped would accept it.
//      A correct dict for an SDK we no longer install still passes.
//
//  (b) The e2e stack wires MockGateway (src/advisor/api/e2e_server.py) for
//      every request, so no e2e spec has ever executed the real translation
//      against the real SDK. This is deliberate and should stay that way —
//      e2e must not spend API credit or need a key — but it does mean the
//      end-to-end suite is structurally incapable of catching an SDK break
//      through the normal chat surface. Adding a chat spec would not have
//      helped; it would only have looked like it did.
//
// So this spec deliberately does NOT go through the UI. It uses the one e2e
// surface that touches reality: POST /v1/_test/llm-registry-probe spawns a
// fresh interpreter which imports the *installed* anthropic package, runs the
// real translation, and compares the emitted kwargs against the real
// `AsyncMessages.create` / `.stream` signatures. No API call, no key, no
// network — just `inspect.signature` against whatever the image actually ships.
//
// This is the assertion that goes red on an image built with anthropic 1.x,
// and it goes red in CI rather than in a user's case.

import { expect, test } from "@playwright/test";

const E2E_API_URL = process.env.E2E_API_URL ?? "http://127.0.0.1:8001";
const PROBE = `${E2E_API_URL}/v1/_test/llm-registry-probe`;

type ProbeResult = {
  returncode: number;
  stderr_tail: string;
  anthropic_version?: string;
  sdk_params_compatible: boolean | null;
  sdk_unsupported_params?: { create: string[]; stream: string[] };
  sdk_probe_error?: string;
};

async function probe(
  request: import("@playwright/test").APIRequestContext,
): Promise<ProbeResult> {
  // No provider and no env: the compatibility check runs unconditionally and
  // needs neither, so the probe is free to fail on the missing key.
  const response = await request.post(PROBE, { data: { provider: null, env: {} } });
  expect(
    response.status(),
    `registry probe failed: ${await response.text()}`,
  ).toBe(200);
  return (await response.json()) as ProbeResult;
}

// The probe spawns an interpreter that imports the advisor package.
test.describe.configure({ timeout: 60_000 });

test.describe("ABS-531 Anthropic SDK parameter compatibility", () => {
  test("every kwarg the gateway sends is accepted by the installed SDK", async ({
    request,
  }) => {
    const result = await probe(request);

    // A null means the check itself could not run — treat that as a failure
    // rather than a pass. A compatibility guard that silently skips is worse
    // than no guard, because it reads as green.
    expect(
      result.sdk_params_compatible,
      `the compatibility probe did not run: ${result.sdk_probe_error}`,
    ).not.toBeNull();

    expect(
      result.sdk_params_compatible,
      `anthropic ${result.anthropic_version} does not accept ` +
        `${JSON.stringify(result.sdk_unsupported_params)} — these are sent on ` +
        `every request and raise TypeError at request time, not at boot. ` +
        `Either pin the SDK back or update _to_anthropic_params for the new ` +
        `surface (see ABS-531).`,
    ).toBe(true);

    expect(result.sdk_unsupported_params?.create).toEqual([]);
    // stream() is asserted separately and on purpose: AnthropicGateway.stream()
    // shares _to_anthropic_params with complete(), so a dropped parameter takes
    // streaming chat down with it. Checking only create() would half-cover the
    // outage this spec exists to prevent.
    expect(result.sdk_unsupported_params?.stream).toEqual([]);
  });

  test("the probe reports the SDK version it actually checked", async ({
    request,
  }) => {
    // Without this the spec above could pass against an SDK nobody can name,
    // which is precisely the condition that caused the outage: the version in
    // the image was known to no one until prod threw.
    const result = await probe(request);

    expect(result.anthropic_version, result.sdk_probe_error).toBeTruthy();
    expect(result.anthropic_version).toMatch(/^\d+\.\d+\.\d+/);
  });
});
