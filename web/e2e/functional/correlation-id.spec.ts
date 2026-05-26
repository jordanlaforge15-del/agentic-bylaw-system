// ABS-133: Correlation-ID middleware e2e tests.
//
// Verifies that the FastAPI backend returns an X-Correlation-ID header
// on every response, and that a caller-supplied correlation ID is
// echoed back (pass-through from upstream proxy / load balancer).

import { expect, test } from "../fixtures/test-env";
import { E2E_API_URL } from "../fixtures/test-env";

test("healthz response includes X-Correlation-ID header", async () => {
  const res = await fetch(`${E2E_API_URL}/healthz`);
  expect(res.status).toBe(200);
  const cid = res.headers.get("x-correlation-id");
  expect(cid).toBeTruthy();
  // Must be a 32-char hex string (uuid4 without dashes)
  expect(cid).toMatch(/^[0-9a-f]{32}$/);
});

test("caller-supplied X-Correlation-ID is echoed back", async () => {
  const customId = "e2e-test-cid-abc123";
  const res = await fetch(`${E2E_API_URL}/healthz`, {
    headers: { "X-Correlation-ID": customId },
  });
  expect(res.status).toBe(200);
  expect(res.headers.get("x-correlation-id")).toBe(customId);
});

test("each request gets a unique correlation ID", async () => {
  const [res1, res2] = await Promise.all([
    fetch(`${E2E_API_URL}/healthz`),
    fetch(`${E2E_API_URL}/readyz`),
  ]);
  const cid1 = res1.headers.get("x-correlation-id");
  const cid2 = res2.headers.get("x-correlation-id");
  expect(cid1).toBeTruthy();
  expect(cid2).toBeTruthy();
  expect(cid1).not.toBe(cid2);
});
