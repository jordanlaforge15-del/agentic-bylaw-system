// Functional: ABS-128 aggregate analytics dashboard API endpoints.
//
// Tests the five new admin analytics endpoints against the e2e test
// stack. The e2e-up.sh script sets ADVISOR_ADMIN_API_ENABLED=true and
// ADVISOR_ADMIN_CLERK_USER_IDS=demo-user-1, so the demo user can
// reach admin endpoints via X-Test-User-Id.
//
// These are API-level tests rather than page-level because the admin
// page's requireAdmin() uses Clerk auth (blanked in e2e). The frontend
// rendering is a thin server component over these endpoints.

import { expect, test } from "@playwright/test";

import { DEMO_USER_ID, E2E_API_URL } from "../fixtures/test-env";

const ADMIN_HEADERS = {
  "X-Test-User-Id": DEMO_USER_ID,
  Accept: "application/json",
};

test("GET /analytics/active-users returns daily and weekly arrays", async ({
  request,
}) => {
  const res = await request.get(
    `${E2E_API_URL}/v1/admin/analytics/active-users`,
    { headers: ADMIN_HEADERS },
  );
  expect(res.status()).toBe(200);
  const body = (await res.json()) as {
    daily: { date: string; count: number }[];
    weekly: { week: string; count: number }[];
  };
  expect(Array.isArray(body.daily)).toBe(true);
  expect(Array.isArray(body.weekly)).toBe(true);
  for (const row of body.daily) {
    expect(typeof row.date).toBe("string");
    expect(typeof row.count).toBe("number");
    expect(row.count).toBeGreaterThanOrEqual(0);
  }
  for (const row of body.weekly) {
    expect(row.week).toMatch(/^\d{4}-W\d{2}$/);
    expect(row.count).toBeGreaterThanOrEqual(0);
  }
});

test("GET /analytics/engagement returns sessions_per_user and messages_per_session", async ({
  request,
}) => {
  const res = await request.get(
    `${E2E_API_URL}/v1/admin/analytics/engagement`,
    { headers: ADMIN_HEADERS },
  );
  expect(res.status()).toBe(200);
  const body = (await res.json()) as {
    sessions_per_user: { week: string; value: number }[];
    messages_per_session: { week: string; value: number }[];
  };
  expect(Array.isArray(body.sessions_per_user)).toBe(true);
  expect(Array.isArray(body.messages_per_session)).toBe(true);
  for (const row of body.sessions_per_user) {
    expect(row.week).toMatch(/^\d{4}-W\d{2}$/);
    expect(typeof row.value).toBe("number");
  }
});

test("GET /analytics/retention returns cohorts with retention percentages", async ({
  request,
}) => {
  const res = await request.get(
    `${E2E_API_URL}/v1/admin/analytics/retention`,
    { headers: ADMIN_HEADERS },
  );
  expect(res.status()).toBe(200);
  const body = (await res.json()) as {
    cohorts: {
      cohort_week: string;
      signup_count: number;
      retention_pcts: number[];
    }[];
    week_labels: string[];
  };
  expect(Array.isArray(body.cohorts)).toBe(true);
  expect(Array.isArray(body.week_labels)).toBe(true);
  expect(body.week_labels.length).toBeGreaterThan(0);
  expect(body.week_labels[0]).toBe("W+1");
  for (const cohort of body.cohorts) {
    expect(cohort.cohort_week).toMatch(/^\d{4}-W\d{2}$/);
    expect(cohort.signup_count).toBeGreaterThanOrEqual(1);
    expect(cohort.retention_pcts.length).toBe(body.week_labels.length);
    for (const pct of cohort.retention_pcts) {
      expect(pct).toBeGreaterThanOrEqual(0);
      expect(pct).toBeLessThanOrEqual(100);
    }
  }
});

test("GET /analytics/credit-trends returns daily credit consumption", async ({
  request,
}) => {
  const res = await request.get(
    `${E2E_API_URL}/v1/admin/analytics/credit-trends`,
    { headers: ADMIN_HEADERS },
  );
  expect(res.status()).toBe(200);
  const body = (await res.json()) as {
    daily: { date: string; tier: string; count: number }[];
  };
  expect(Array.isArray(body.daily)).toBe(true);
  for (const row of body.daily) {
    expect(typeof row.date).toBe("string");
    expect(["quick", "standard", "complex"]).toContain(row.tier);
    expect(row.count).toBeGreaterThanOrEqual(1);
  }
});

test("GET /analytics/funnel returns ordered lifecycle stages", async ({
  request,
}) => {
  const res = await request.get(
    `${E2E_API_URL}/v1/admin/analytics/funnel`,
    { headers: ADMIN_HEADERS },
  );
  expect(res.status()).toBe(200);
  const body = (await res.json()) as {
    stages: { name: string; count: number }[];
  };
  expect(body.stages).toHaveLength(5);
  const names = body.stages.map((s) => s.name);
  expect(names).toEqual([
    "Signed up",
    "Accepted terms",
    "First question",
    "Repeat user",
    "Purchased",
  ]);
  // The seeded demo user should show up in at least "Signed up"
  expect(body.stages[0].count).toBeGreaterThanOrEqual(1);
  for (const stage of body.stages) {
    expect(stage.count).toBeGreaterThanOrEqual(0);
  }
});

test("analytics endpoints reject non-admin user", async ({ request }) => {
  const nonAdminHeaders = {
    "X-Test-User-Id": "not-an-admin-user",
    Accept: "application/json",
  };
  const endpoints = [
    "/v1/admin/analytics/active-users",
    "/v1/admin/analytics/engagement",
    "/v1/admin/analytics/retention",
    "/v1/admin/analytics/credit-trends",
    "/v1/admin/analytics/funnel",
  ];
  for (const ep of endpoints) {
    const res = await request.get(`${E2E_API_URL}${ep}`, {
      headers: nonAdminHeaders,
    });
    expect(
      res.status(),
      `expected 403 for ${ep} but got ${res.status()}`,
    ).toBe(403);
  }
});
