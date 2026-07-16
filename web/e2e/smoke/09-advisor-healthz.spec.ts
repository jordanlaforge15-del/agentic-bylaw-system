// ABS-85: Advisor import smoke — Playwright analog of scripts/preflight_advisor_image.sh.
//
// /healthz returning 200 with status=ok and database=ok proves that
// advisor.api.main was successfully imported and build_app() completed,
// which is exactly what the pre-deploy preflight gate checks. Any
// missing [advisor] extras (v0.8.3 class) or startup filesystem write
// (v0.8.4 class) would prevent the server from starting at all, so
// this endpoint would never respond.

import { E2E_API_URL } from "../fixtures/test-env";
import { expect, test } from "../fixtures/test-env";

test.describe("advisor import smoke", () => {
  test("/healthz responds ok — app imported and initialized successfully", async () => {
    const res = await fetch(`${E2E_API_URL}/healthz`);
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.status).toBe("ok");
    expect(body.checks.database).toBe("ok");
  });
});
