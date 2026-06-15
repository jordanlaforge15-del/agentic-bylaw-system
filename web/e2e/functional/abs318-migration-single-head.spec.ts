// ABS-318 — Alembic single-head guard (collision-resistant migrations).
//
// Validates that the e2e stack started with a clean, single-head Alembic
// state. The guard in scripts/e2e-up.sh aborts startup when >1 head is
// detected; if that guard had not fired, a dual-head chain would cause
// `alembic upgrade head` to fail and the stack would never reach a state
// where these assertions could pass.
//
// Two checks:
//  (a) /healthz returns database=ok  — proves migrations ran to completion.
//  (b) POST /v1/cases succeeds        — proves the schema is fully applied
//      (the cases table and its FK columns require every migration up to HEAD).

import { E2E_API_URL, DEMO_USER_ID, test, expect } from "../fixtures/test-env";

test.describe("Alembic single-head guard (ABS-318)", () => {
  test("(a) /healthz reports database=ok after migrations", async () => {
    const res = await fetch(`${E2E_API_URL}/healthz`);
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.status).toBe("ok");
    expect(body.checks.database).toBe("ok");
  });

  test("(b) POST /v1/cases succeeds — schema fully applied by migrations", async () => {
    const res = await fetch(`${E2E_API_URL}/v1/cases`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Test-User-Id": DEMO_USER_ID,
      },
      body: JSON.stringify({
        anchor_label: `abs318-smoke-${Math.random().toString(36).slice(2, 8)}`,
        anchor_kind: "address",
        tier: "standard",
      }),
    });
    expect(res.status).toBe(200);
    const data = (await res.json()) as { case: { id: number } };
    expect(typeof data.case.id).toBe("number");
  });
});
