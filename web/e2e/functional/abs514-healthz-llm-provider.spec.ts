// ABS-514: /healthz reports the provider that is actually serving traffic.
//
// The incident: an eval run was driven against a locally-started advisor
// that had silently resolved to the metered `anthropic` provider. /healthz
// was checked after boot and looked fine — it reported `main_model` and
// nothing else, so a metered boot was indistinguishable from a
// non-metered one. Eight cases and ~$1.70 later, the provider was noticed.
//
// The e2e stack boots `advisor.api.e2e_server`, which always wires a
// MockGateway. So this suite is the one place we can assert the *negative*
// end-to-end: a real HTTP response from a real advisor process, proving the
// field tracks the constructed gateway rather than ADVISOR_LLM_PROVIDER
// (which is unset or `anthropic` here, and must not be what's reported).

import { E2E_API_URL } from "../fixtures/test-env";
import { expect, test } from "../fixtures/test-env";

test.describe("ABS-514 healthz LLM provider", () => {
  test("/healthz reports the resolved gateway provider, not the env var", async () => {
    const res = await fetch(`${E2E_API_URL}/healthz`);
    expect(res.status).toBe(200);
    const body = await res.json();

    // The e2e server constructs MockGateway unconditionally. Reading the
    // env var instead would report "anthropic" here — the exact drift
    // this field exists to make impossible.
    expect(body.llm.provider).toBe("mock");
    expect(body.llm.main_model).toBeTruthy();
  });

  test("/healthz reports API-key presence as a boolean and leaks no key", async () => {
    const res = await fetch(`${E2E_API_URL}/healthz`);
    const raw = await res.text();
    const body = JSON.parse(raw);

    // Presence is the single fact that decides whether a turn meters, so
    // it is reported; the value never is.
    expect(typeof body.llm.anthropic_api_key_present).toBe("boolean");
    expect(raw).not.toContain("sk-ant-");
  });
});
