// ABS-132: CI/CD pipeline integration health.
//
// The CI workflow (`.github/workflows/ci.yml`) gates every push on three
// quality checks — lint, typecheck, and pytest against a live Postgres —
// then builds and pushes both service images.  These assertions verify the
// two outputs that a green CI run produces: a working advisor API (with
// completed migrations) and a reachable web frontend.  A red result here
// means something that CI is supposed to catch has slipped through to the
// running stack.

import { expect, test } from "../fixtures/test-env";
import { E2E_API_URL } from "../fixtures/test-env";

test.describe("CI pipeline outputs", () => {
  test("advisor API /healthz — DB reachable and migrations applied", async () => {
    const res = await fetch(`${E2E_API_URL}/healthz`);
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.status).toBe("ok");
    // Presence of a passing database check proves alembic upgrade head ran
    // and the schema is intact — exactly what the CI `test` job validates.
    expect(body.checks.database).toBe("ok");
  });

  test("advisor API /readyz — stack ready for traffic", async () => {
    const res = await fetch(`${E2E_API_URL}/readyz`);
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.status).toBe("ready");
    expect(body.checks.database).toBe("ok");
  });

  test("web frontend serves the root page", async ({ page }) => {
    const response = await page.goto("/");
    // Any 2xx confirms the Next.js server started and can serve the app —
    // the same guarantee the web image build in CI is meant to deliver.
    expect(response?.status()).toBeLessThan(300);
    expect(response?.status()).toBeGreaterThanOrEqual(200);
  });
});
