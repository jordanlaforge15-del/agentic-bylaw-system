// ABS-59: Speckle Automate integration — API-key-authenticated submissions.
//
// Tests the /v1/integrations/submissions endpoint which accepts an
// X-ABS-API-Key header instead of a Clerk JWT.  This proves the full
// M2M path the Speckle Automate function takes:
//   1. Issue an API key for a test user via the e2e test helper.
//   2. Upload an IFC to /v1/integrations/submissions with the key.
//   3. Trigger the evaluator via /v1/integrations/submissions/{id}/evaluate.
//   4. Fetch the compliance matrix.
//
// All requests go directly to the FastAPI e2e server (E2E_API_URL) — no
// browser UI is involved.  Single test, desktop-chrome only.

import { execSync } from "node:child_process";
import * as fs from "node:fs";
import * as path from "node:path";

import { expect, test, E2E_API_URL } from "../fixtures/test-env";

const TEST_PID = "E2E00100"; // seeded by seed_e2e_evaluator_bylaws.py

test.describe("integrations API key submissions (ABS-59)", () => {
  test.use({ project: "desktop-chrome" } as never);

  test.beforeAll(() => {
    // Seed the parcel + bylaw corpus the evaluator needs.
    const repoRoot = path.resolve(__dirname, "..", "..", "..");
    const seed = path.join(
      repoRoot,
      "scripts",
      "seed_e2e_evaluator_bylaws.py",
    );
    const venvPython = path.join(repoRoot, ".venv", "bin", "python");
    const databaseUrl =
      process.env.DATABASE_URL ||
      "postgresql+psycopg://layer1:layer1@localhost:5432/layer1_test";
    execSync(`"${venvPython}" "${seed}"`, {
      env: {
        ...process.env,
        DATABASE_URL: databaseUrl,
        PYTHONPATH: `${path.join(repoRoot, "src")}:${process.env.PYTHONPATH || ""}`,
      },
      stdio: "inherit",
    });
  });

  test("issue API key → upload IFC → evaluate → matrix", async ({ page }) => {
    const repoRoot = path.resolve(__dirname, "..", "..", "..");
    const ifcPath = path.join(
      repoRoot,
      "web",
      "e2e",
      "fixtures",
      "submission-demo.ifc",
    );

    // ------------------------------------------------------------------
    // 1. Create test user + issue API key
    // ------------------------------------------------------------------
    const testUserId = `speckle-e2e-${Date.now()}`;

    // Create the user row by hitting any authed endpoint (the e2e user
    // dependency JIT-creates the row on first contact).
    const pingResp = await page.request.get(`${E2E_API_URL}/healthz`);
    expect(pingResp.ok()).toBeTruthy();

    // Trigger JIT user creation via a cases list call.
    await page.request.get(`${E2E_API_URL}/v1/cases`, {
      headers: { "X-Test-User-Id": testUserId },
    });

    // Issue the API key for this test user.
    const issueResp = await page.request.post(
      `${E2E_API_URL}/v1/_test/issue-api-key`,
      {
        data: { clerk_user_id: testUserId, name: "speckle-automate-e2e" },
      },
    );
    expect(issueResp.ok(), `issue-api-key: ${await issueResp.text()}`).toBeTruthy();
    const { raw_key: rawKey } = await issueResp.json();
    expect(rawKey).toBeTruthy();

    // ------------------------------------------------------------------
    // 2. Upload IFC with the API key
    // ------------------------------------------------------------------
    const ifcBytes = fs.readFileSync(ifcPath);
    const uploadResp = await page.request.post(
      `${E2E_API_URL}/v1/integrations/submissions`,
      {
        headers: { "X-ABS-API-Key": rawKey },
        multipart: {
          file: {
            name: "submission-demo.ifc",
            mimeType: "application/octet-stream",
            buffer: ifcBytes,
          },
          parcel_address: TEST_PID,
        },
      },
    );
    expect(
      uploadResp.ok(),
      `upload: ${await uploadResp.text()}`,
    ).toBeTruthy();

    const submission = await uploadResp.json();
    expect(submission.id).toBeGreaterThan(0);
    expect(submission.status).toBe("draft");
    const attributeKeys = (submission.attributes as Array<{ attribute_key: string }>).map(
      (a) => a.attribute_key,
    );
    expect(attributeKeys).toContain("building_height_m");

    // ------------------------------------------------------------------
    // 3. Trigger evaluator
    // ------------------------------------------------------------------
    const evalResp = await page.request.post(
      `${E2E_API_URL}/v1/integrations/submissions/${submission.id}/evaluate`,
      { headers: { "X-ABS-API-Key": rawKey } },
    );
    expect(evalResp.ok(), `evaluate: ${await evalResp.text()}`).toBeTruthy();
    const evalBody = await evalResp.json();
    expect(evalBody.submission_id).toBe(submission.id);

    // ------------------------------------------------------------------
    // 4. Fetch compliance matrix
    // ------------------------------------------------------------------
    const matrixResp = await page.request.get(
      `${E2E_API_URL}/v1/integrations/submissions/${submission.id}/matrix`,
      { headers: { "X-ABS-API-Key": rawKey } },
    );
    expect(matrixResp.ok(), `matrix: ${await matrixResp.text()}`).toBeTruthy();
    const matrix = await matrixResp.json();
    expect(matrix).toBeTruthy();

    // ------------------------------------------------------------------
    // 5. Verify a wrong key is rejected
    // ------------------------------------------------------------------
    const rejectedResp = await page.request.get(
      `${E2E_API_URL}/v1/integrations/submissions/${submission.id}/matrix`,
      { headers: { "X-ABS-API-Key": "not-a-real-key" } },
    );
    expect(rejectedResp.status()).toBe(401);
  });
});
