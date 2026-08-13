// ABS-87: writable storage for submission uploads.
//
// The prod advisor container is `read_only: true`, so POST /v1/submissions
// only works when a writable volume is mounted at SUBMISSION_STORAGE_DIR.
// ABS-70 surfaced the startup half of that gap (an eager mkdir crashed the
// container on boot); the request half — the first real upload 500ing with
// an OSError — is what this issue closes.
//
// These tests pin the two guarantees that make the fix verifiable in prod:
//
//   1. /healthz reports `checks.submission_storage`, so a missing volume is
//      a one-curl post-deploy check rather than something the first user
//      discovers. It must NOT be status-fatal — the availability monitor
//      pages on a non-200 and submissions aren't on the chat critical path.
//   2. A real IFC upload returns 2xx with a persisted submission row whose
//      artefact was staged under the storage root (the issue's acceptance
//      criterion), inside the uploader's own directory — a client-supplied
//      filename can't traverse out of it.
//
// API-level, no browser UI (submission-upload.spec.ts covers the UI flow).

import { execSync } from "node:child_process";
import * as fs from "node:fs";
import * as path from "node:path";

import { expect, test, E2E_API_URL } from "../fixtures/test-env";
import { resolveDatabaseUrl } from "../helpers/database-url";

const TEST_PID = "E2E00100"; // seeded by seed_e2e_evaluator_bylaws.py

test.describe("submission storage writability (ABS-87)", () => {
  test.use({ project: "desktop-chrome" } as never);

  test.beforeAll(() => {
    const repoRoot = path.resolve(__dirname, "..", "..", "..");
    const seed = path.join(
      repoRoot,
      "scripts",
      "seed_e2e_evaluator_bylaws.py",
    );
    const venvPython = path.join(repoRoot, ".venv", "bin", "python");
    // ABS-207: honor PG_PORT for the parallel-worktree case.
    const databaseUrl = resolveDatabaseUrl();
    execSync(`"${venvPython}" "${seed}"`, {
      env: {
        ...process.env,
        DATABASE_URL: databaseUrl,
        PYTHONPATH: `${path.join(repoRoot, "src")}:${process.env.PYTHONPATH || ""}`,
      },
      stdio: "inherit",
    });
  });

  test("/healthz reports submission storage as writable without gating status", async () => {
    const res = await fetch(`${E2E_API_URL}/healthz`);
    expect(res.status).toBe(200);
    const body = await res.json();
    // The e2e stack runs against a writable working tree.
    expect(body.checks.submission_storage).toBe("ok");
    // Storage health is reported, not fatal.
    expect(body.status).toBe("ok");
  });

  test("IFC upload returns 2xx and stages the artefact under the storage root", async ({
    page,
  }) => {
    const repoRoot = path.resolve(__dirname, "..", "..", "..");
    const ifcBytes = fs.readFileSync(
      path.join(repoRoot, "web", "e2e", "fixtures", "submission-demo.ifc"),
    );
    const userId = `abs87-storage-${Date.now()}`;

    const uploadResp = await page.request.post(
      `${E2E_API_URL}/v1/submissions`,
      {
        headers: { "X-Test-User-Id": userId },
        multipart: {
          file: {
            name: "abs87-demo.ifc",
            mimeType: "application/octet-stream",
            buffer: ifcBytes,
          },
          parcel_address: TEST_PID,
        },
      },
    );

    // The regression this issue exists for: a missing writable volume made
    // this a 500 (OSError) — and, post-fix, a 503 naming the path.
    expect(
      uploadResp.status(),
      `upload: ${await uploadResp.text()}`,
    ).toBe(200);

    const submission = await uploadResp.json();
    expect(submission.id).toBeGreaterThan(0);
    expect(submission.status).toBe("draft");
    expect(
      (submission.attributes as Array<{ attribute_key: string }>).map(
        (a) => a.attribute_key,
      ),
    ).toContain("building_height_m");

    // The artefact was staged on disk under the per-user directory.
    expect(submission.source_artifact_path).toContain("/user-");
    expect(submission.source_artifact_path).toContain("abs87-demo.ifc");

    // The row persisted — the acceptance criterion is a 2xx *with* a
    // submission row, not just a 2xx.
    const fetched = await page.request.get(
      `${E2E_API_URL}/v1/submissions/${submission.id}`,
      { headers: { "X-Test-User-Id": userId } },
    );
    expect(fetched.ok(), `fetch: ${await fetched.text()}`).toBeTruthy();
    expect((await fetched.json()).id).toBe(submission.id);
  });

  test("a traversing filename cannot escape the uploader's directory", async ({
    page,
  }) => {
    const repoRoot = path.resolve(__dirname, "..", "..", "..");
    const ifcBytes = fs.readFileSync(
      path.join(repoRoot, "web", "e2e", "fixtures", "submission-demo.ifc"),
    );
    const userId = `abs87-traversal-${Date.now()}`;

    const uploadResp = await page.request.post(
      `${E2E_API_URL}/v1/submissions`,
      {
        headers: { "X-Test-User-Id": userId },
        multipart: {
          file: {
            name: "../../../abs87-escaped.ifc",
            mimeType: "application/octet-stream",
            buffer: ifcBytes,
          },
          parcel_address: TEST_PID,
        },
      },
    );
    expect(
      uploadResp.status(),
      `upload: ${await uploadResp.text()}`,
    ).toBe(200);

    const artifactPath = (await uploadResp.json()).source_artifact_path;
    expect(artifactPath).not.toContain("..");
    expect(artifactPath).toMatch(/\/user-\d+\/abs87-escaped\.ifc$/);
  });
});
