// ABS-123: Deep health check endpoints.
//
// /healthz returns 200 + status=ok when the DB is reachable (the e2e
// stack always has a live Postgres). /readyz returns 200 + status=ready
// under the same conditions. Both endpoints are unauthenticated.

import { expect, test } from "../fixtures/test-env";
import { E2E_API_URL } from "../fixtures/test-env";

test("/healthz returns ok with database check", async () => {
  const res = await fetch(`${E2E_API_URL}/healthz`);
  expect(res.status).toBe(200);
  const body = await res.json();
  expect(body.status).toBe("ok");
  expect(body.checks.database).toBe("ok");
});

test("/readyz returns ready when database is reachable", async () => {
  const res = await fetch(`${E2E_API_URL}/readyz`);
  expect(res.status).toBe(200);
  const body = await res.json();
  expect(body.status).toBe("ready");
  expect(body.checks.database).toBe("ok");
});
