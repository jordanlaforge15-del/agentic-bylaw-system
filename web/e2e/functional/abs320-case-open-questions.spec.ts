// Functional: ABS-320 — /cases/new sells PRICED QUESTIONS, not tiers.
//
// The product pivoted from the quick/standard/complex tier model to a
// priced-question catalog ("buy an answer"). This spec pins the migrated
// primary entry flow: the case-open form renders the live question menu
// (GET /api/billing/questions — the 5 launch questions + an "Other"
// option), keeps the free anchor input + in-window match behaviour, and
// shows none of the retired tier / reasoning-step / classifier copy.
//
// Billing is dormant in the e2e stack (no Stripe), so the catalog marks
// every question `available: false`. The default demo user is seeded with
// 3 free questions (global-setup.ts --free-questions 3), so the form
// shows "Get answer (free trial)" instead of a checkout button.
//
// API-level tests verify the free-start endpoint consumes the entitlement
// and returns a case_id (happy path) and returns 402 when exhausted.

import { execSync } from "node:child_process";
import * as path from "node:path";

import {
  E2E_API_URL,
  expect,
  openCaseViaApi,
  test,
} from "../fixtures/test-env";
import { DEMO_USER_ID } from "../fixtures/test-env";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeUserId(suffix: string): string {
  return `abs320-${suffix}-${Date.now()}`;
}

function seedUser(opts: {
  userId: string;
  freeQuestions: number;
}): void {
  const repoRoot = path.resolve(__dirname, "..", "..", "..");
  const seed = path.join(repoRoot, "scripts", "seed_e2e_user.py");
  const venvPython = path.join(repoRoot, ".venv", "bin", "python");
  const pgPort = process.env.PG_PORT || "5432";
  const databaseUrl =
    process.env.DATABASE_URL ||
    `postgresql+psycopg://layer1:layer1@localhost:${pgPort}/layer1_test`;
  execSync(
    `"${venvPython}" "${seed}" ` +
      `--user-id "${opts.userId}" ` +
      `--email "${opts.userId}@e2e.test" ` +
      `--credits-per-tier 0 ` +
      `--free-questions ${opts.freeQuestions}`,
    {
      env: {
        ...process.env,
        DATABASE_URL: databaseUrl,
        PYTHONPATH: `${path.join(repoRoot, "src")}:${
          process.env.PYTHONPATH || ""
        }`,
      },
      stdio: "inherit",
    },
  );
}

// ---------------------------------------------------------------------------
// Browser tests (demo user: 3 free questions, billing dormant)
// ---------------------------------------------------------------------------

test("case-open form renders the priced-question menu, not tiers", async ({
  page,
}) => {
  await page.goto("/cases/new");

  // The question menu loads from the live catalog.
  await expect(page.getByTestId("question-menu")).toBeVisible();

  // The 5 launch questions are present, with grounded prices, plus "Other".
  await expect(
    page.getByTestId("question-option-permitted_use"),
  ).toBeVisible();
  await expect(
    page.getByTestId("question-option-development_standards"),
  ).toBeVisible();
  await expect(
    page.getByTestId("question-option-due_diligence"),
  ).toBeVisible();
  await expect(
    page.getByTestId("question-option-legal_nonconforming"),
  ).toBeVisible();
  await expect(
    page.getByTestId("question-option-variance_justification"),
  ).toBeVisible();
  await expect(page.getByTestId("question-option-other")).toBeVisible();

  // The cheapest launch question shows its $79 anchor price.
  await expect(
    page.getByTestId("question-option-permitted_use"),
  ).toContainText("$79");

  // The retired tier / classifier / reasoning-step model is gone.
  const body = await page.textContent("body");
  if (body) {
    expect(body).not.toContain("Get tier recommendation");
    expect(body).not.toContain("reasoning steps");
    expect(body).not.toContain("Open at tier");
    expect(body).not.toContain("CLASSIFIER RECOMMENDS");
  }
});

test("billing dormant + free credits: shows free-trial button, not 'not configured'", async ({
  page,
}) => {
  // The default demo user has 3 free questions (seeded in global-setup).
  // Billing is dormant (no Stripe in e2e), so questions are `available:false`,
  // but the form should show the free-trial CTA instead of a "not configured"
  // notice.
  await page.goto("/cases/new");
  await expect(page.getByTestId("question-menu")).toBeVisible();

  await page.getByTestId("question-option-permitted_use").click();
  await expect(page.getByText(/Describe your situation/i)).toBeVisible();

  // Free-trial button visible; "not configured" notice absent.
  await expect(page.getByTestId("free-trial-btn")).toBeVisible();
  await expect(
    page.getByText(/Checkout isn.?t configured for this question yet/i),
  ).not.toBeVisible();
  await expect(page.getByTestId("free-trial-exhausted")).not.toBeVisible();
});

test("billing dormant + zero free credits: shows exhausted message", async ({
  page,
}) => {
  // Intercept the billing/me call at the browser level so we don't have
  // to fight the JWT-cookie pipeline that the e2e mock-Clerk stack uses.
  // The cookie-override approach is unreliable here because
  // isClerkConfigured() returns true in e2e (CLERK_SECRET_KEY is set to
  // the mock key), so buildAdvisorAuthHeaders() uses auth().getToken()
  // rather than the X-Test-User-Id header path.
  await page.route("**/api/billing/me", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        enabled: false,
        stripe_customer_id: null,
        tier_balances: [],
        total_available_credits: 0,
        free_questions_remaining: 0,
      }),
    }),
  );

  await page.goto("/cases/new");
  await expect(page.getByTestId("question-menu")).toBeVisible();

  await page.getByTestId("question-option-permitted_use").click();
  await expect(page.getByText(/Describe your situation/i)).toBeVisible();

  // Exhausted message visible; free-trial button absent.
  await expect(page.getByTestId("free-trial-exhausted")).toBeVisible();
  await expect(
    page.getByText(/Free trial used.*paid answers coming soon/i),
  ).toBeVisible();
  await expect(page.getByTestId("free-trial-btn")).not.toBeVisible();
});

test("the Other path shows quote step then free-trial button (billing dormant)", async ({
  page,
}) => {
  await page.goto("/cases/new");
  await expect(page.getByTestId("question-menu")).toBeVisible();

  await page.getByTestId("question-option-other").click();
  await expect(page.getByText(/Describe your question/i)).toBeVisible();

  // With billing dormant and free credits, the user still gets the quote
  // step first (always free), then will see the free-trial buy button.
  await expect(page.getByText(/Get a price \(free\)/i)).toBeVisible();
  // The "Off-menu checkout isn't configured yet" message is replaced by
  // the free-trial UX — no checkout error visible upfront.
  await expect(
    page.getByText(/Off-menu checkout isn.?t configured yet/i),
  ).not.toBeVisible();
});

test("anchor still surfaces an in-window existing-case match", async ({
  page,
}) => {
  // A case is a free container — opening/continuing one never charges. The
  // match lookup that powers "continue without buying again" is preserved.
  const anchor = `abs320-match-${Date.now()}`;
  await openCaseViaApi({ anchorLabel: anchor });

  await page.goto("/cases/new");
  const anchorInput = page.getByPlaceholder(/1234 Main St, Halifax/);
  await anchorInput.fill(anchor);
  await anchorInput.blur();

  await expect(page.getByText(/EXISTING CASE FOUND/)).toBeVisible();
  await expect(
    page.getByRole("button", { name: /Continue case/ }),
  ).toBeVisible();
});

// ---------------------------------------------------------------------------
// API tests: free-start endpoint (ABS-314 / ABS-320 / ABS-322)
// ---------------------------------------------------------------------------

test("free-start API: consumes one credit and returns case_id", async ({
  request,
}) => {
  // Seed a fresh user with 1 free question.
  const userId = makeUserId("fresh");
  seedUser({ userId, freeQuestions: 1 });

  const res = await request.post(`${E2E_API_URL}/v1/billing/questions/free-start`, {
    headers: { "X-Test-User-Id": userId },
    data: {
      question_slug: "permitted_use",
      inputs: { address: "123 Test St, Halifax" },
      anchor_label: "123 Test St, Halifax",
      anchor_kind: "address",
    },
  });

  expect(
    res.status(),
    `expected 200 but got ${res.status()}: ${await res.text()}`,
  ).toBe(200);

  const body = (await res.json()) as {
    case_id: number;
    anchor_label: string;
    free_questions_remaining: number;
  };

  expect(body.case_id, "case_id should be a positive integer").toBeGreaterThan(0);
  expect(
    body.free_questions_remaining,
    "counter should be decremented to 0 after consuming the single credit",
  ).toBe(0);
});

test("free-start API: returns 402 when free credits exhausted", async ({
  request,
}) => {
  // Seed a user with 0 free questions.
  const userId = makeUserId("exhausted");
  seedUser({ userId, freeQuestions: 0 });

  const res = await request.post(`${E2E_API_URL}/v1/billing/questions/free-start`, {
    headers: { "X-Test-User-Id": userId },
    data: {
      question_slug: "permitted_use",
      inputs: {},
      anchor_label: "456 Oak Ave, Dartmouth",
      anchor_kind: "address",
    },
  });

  expect(
    res.status(),
    "exhausted counter should return 402 Payment Required",
  ).toBe(402);

  const body = (await res.json()) as { detail: { code: string } };
  expect(body.detail.code).toBe("free_trial_exhausted");
});
