// Functional: ABS-320 — /cases/new sells PRICED QUESTIONS, not tiers.
//
// The product pivoted from the quick/standard/complex tier model to a
// priced-question catalog ("buy an answer"). This spec pins the migrated
// primary entry flow: the case-open form renders the live question menu
// (GET /api/billing/questions — the 5 launch questions), keeps the free
// anchor input + in-window match behaviour, and shows none of the retired
// tier / reasoning-step / classifier copy.
//
// ABS-325 removed the off-menu "Other" free-form option from the menu;
// absence of that entry point is pinned in abs325-other-disabled.spec.ts.
//
// Billing is dormant in the e2e stack (no Stripe), so the catalog marks
// every question `available: false`. The default demo user is seeded with
// 3 free questions (global-setup.ts --free-questions 3), so the form
// shows "Get answer (free trial)" instead of a checkout button.
//
// API-level tests verify the free-start endpoint consumes the entitlement
// and returns a purchase_id (happy path, ABS-324 decoupled) and returns
// 402 when exhausted.

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

  // The 5 launch questions are present, with grounded prices.
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

test("Answers-only entry: the Conversation continue-case CTA is hidden (ABS-324)", async ({
  page,
}) => {
  // Continuing an existing case routes into the Conversation /app chat. At
  // launch /cases/new is Answers-only (ADVISOR_CONVERSATION_ENTRY_ENABLED
  // off), so even when a matching case exists the "EXISTING CASE FOUND" /
  // "Continue case" entry must NOT surface. The Conversation product stays
  // in the codebase; it's just not the primary in-app door.
  //
  // The e2e server runs with the flag ON (so the legacy continue-case suite
  // can exercise it). This test asserts the production launch posture, so it
  // stubs the question-menu response's conversation_enabled back to false —
  // the exact signal /cases/new reads to decide whether the entry exists.
  await page.route("**/api/billing/questions", async (route) => {
    const resp = await route.fetch();
    const body = await resp.json();
    body.conversation_enabled = false;
    await route.fulfill({ response: resp, json: body });
  });

  const anchor = `abs320-match-${Date.now()}`;
  await openCaseViaApi({ anchorLabel: anchor });

  await page.goto("/cases/new");
  await expect(page.getByTestId("question-menu")).toBeVisible();
  const anchorInput = page.getByPlaceholder(/1234 Main St, Halifax/);
  await anchorInput.fill(anchor);
  await anchorInput.blur();

  // Give any (skipped) match lookup a beat — the CTA must stay absent.
  await expect(page.getByText(/EXISTING CASE FOUND/)).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: /Continue case/ }),
  ).toHaveCount(0);
});

// ---------------------------------------------------------------------------
// API tests: free-start endpoint (ABS-314 / ABS-320 / ABS-322)
// ---------------------------------------------------------------------------

// ABS-324: free-start now opens an Answers QuestionPurchase (not a Case)
// and returns purchase_id for routing to the dedicated answer view. The
// deeper "never touches CaseCredit" assertions live in
// abs324-answers-decoupled.spec.ts.
test("free-start API: consumes one credit and returns purchase_id", async ({
  request,
}) => {
  // Seed a fresh user with 1 free question.
  const userId = makeUserId("fresh");
  seedUser({ userId, freeQuestions: 1 });

  const res = await request.post(`${E2E_API_URL}/v1/billing/questions/free-start`, {
    headers: { "X-Test-User-Id": userId },
    data: {
      question_slug: "permitted_use",
      inputs: {
        address: "123 Test St, Halifax",
        proposed_use: "a duplex",
      },
      anchor_label: "123 Test St, Halifax",
      anchor_kind: "address",
    },
  });

  expect(
    res.status(),
    `expected 200 but got ${res.status()}: ${await res.text()}`,
  ).toBe(200);

  const body = (await res.json()) as {
    purchase_id: number;
    status: string;
    free_questions_remaining: number;
  };

  expect(
    body.purchase_id,
    "purchase_id should be a positive integer",
  ).toBeGreaterThan(0);
  expect(
    body.status,
    "the purchase is born authorized — ready for /answer",
  ).toBe("authorized");
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
      // Inputs are complete so validation passes and the credit check —
      // not input validation — is what rejects: an exhausted trial is a
      // 402, distinct from a 400 unworkable-inputs rejection.
      inputs: {
        address: "456 Oak Ave, Dartmouth",
        proposed_use: "a triplex",
      },
      anchor_label: "456 Oak Ave, Dartmouth",
      anchor_kind: "address",
    },
  });

  expect(
    res.status(),
    "exhausted counter should return 402 Payment Required",
  ).toBe(402);

  const body = (await res.json()) as { detail: { code: string } };
  expect(body.detail.code).toBe("free_questions_exhausted");
});
