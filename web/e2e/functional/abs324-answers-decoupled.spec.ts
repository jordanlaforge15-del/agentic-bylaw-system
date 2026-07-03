// Functional: ABS-324 — the two purchase products are decoupled.
//
// Decision (2026-06-16): keep BOTH products but make the Answers free/paid
// path terminate in its OWN delivery surface (the dedicated answer/refine
// view) and never touch the Conversation CaseCredit ledger. The coupling
// behind ABS-323 was: free-start consumed an Answers credit, then opened a
// Case and routed to /app (a Conversation gate that reserves a CaseCredit).
//
// This spec drives the REAL dormant billing router end-to-end (no stubs):
//
//   free-start → answer → refine
//
// asserting at every step that:
//   * free-start returns a purchase_id (an Answers QuestionPurchase), NOT a
//     case_id — no Case is opened;
//   * the answer is produced and captured in the Answers flow;
//   * NO CaseCredit is reserved or consumed (the Conversation ledger is
//     untouched) and no Case exists for the subject address;
//   * the dedicated /app/answers/{id} view renders the grounded answer.
//
// The answer/refine/intake endpoints are now mounted on the dormant
// (payments-off) router too — that is the decoupling: the Answers product
// is its own thing, live regardless of whether Stripe billing is enabled.

import { execSync } from "node:child_process";
import * as path from "node:path";

import {
  DEMO_USER_ID,
  E2E_API_URL,
  expect,
  test,
} from "../fixtures/test-env";

type Req = import("@playwright/test").APIRequestContext;

const VALID_INPUTS = {
  address: "1234 Elm St, Halifax",
  proposed_use: "a four-unit dwelling",
};

// ABS-343: POST /answer dispatches a background generation job and returns
// `generating`; poll the purchase until the job settles to `captured`.
async function pollUntilCaptured(
  request: Req,
  userId: string,
  purchaseId: number,
): Promise<{ status: string; answer: string | null; refinements_remaining: number }> {
  let last = { status: "unknown", answer: null, refinements_remaining: 0 } as {
    status: string;
    answer: string | null;
    refinements_remaining: number;
  };
  await expect
    .poll(
      async () => {
        const r = await request.get(
          `${E2E_API_URL}/v1/billing/questions/purchases/${purchaseId}`,
          { headers: { "X-Test-User-Id": userId } },
        );
        if (r.status() !== 200) return `http_${r.status()}`;
        last = await r.json();
        return last.status;
      },
      { timeout: 20_000, intervals: [200, 400, 800] },
    )
    .toBe("captured");
  return last;
}

function makeUserId(suffix: string): string {
  return `abs324-${suffix}-${Date.now()}-${Math.random()
    .toString(36)
    .slice(2, 8)}`;
}

function seedUser(userId: string, freeQuestions: number): void {
  const repoRoot = path.resolve(__dirname, "..", "..", "..");
  const seed = path.join(repoRoot, "scripts", "seed_e2e_user.py");
  const venvPython = path.join(repoRoot, ".venv", "bin", "python");
  const pgPort = process.env.PG_PORT || "5432";
  const databaseUrl =
    process.env.DATABASE_URL ||
    `postgresql+psycopg://layer1:layer1@localhost:${pgPort}/layer1_test`;
  execSync(
    `"${venvPython}" "${seed}" ` +
      `--user-id "${userId}" ` +
      `--email "${userId}@e2e.test" ` +
      `--credits-per-tier 0 ` +
      `--free-questions ${freeQuestions}`,
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

async function freeStart(
  request: Req,
  userId: string,
  inputs: Record<string, string>,
  slug = "permitted_use",
) {
  return request.post(`${E2E_API_URL}/v1/billing/questions/free-start`, {
    headers: { "X-Test-User-Id": userId },
    data: {
      question_slug: slug,
      inputs,
      anchor_label: inputs.address ?? "",
      anchor_kind: "address",
    },
  });
}

async function billingMe(request: Req, userId: string) {
  const res = await request.get(`${E2E_API_URL}/v1/billing/me`, {
    headers: { "X-Test-User-Id": userId },
  });
  expect(res.status(), await res.text()).toBe(200);
  return res.json();
}

test("decoupled: free-trial answer is delivered in the Answers flow with zero CaseCredit", async ({
  request,
}) => {
  const userId = makeUserId("e2e");
  seedUser(userId, 1);

  // free-start opens an Answers QuestionPurchase (NOT a Case).
  const startRes = await freeStart(request, userId, VALID_INPUTS);
  expect(startRes.status(), await startRes.text()).toBe(200);
  const start = (await startRes.json()) as {
    purchase_id: number;
    status: string;
    case_id?: number;
    free_questions_remaining: number;
  };
  expect(start.purchase_id).toBeGreaterThan(0);
  expect(start.status).toBe("authorized");
  // The decoupling: no Case identifier is handed back anymore.
  expect(start.case_id).toBeUndefined();
  expect(start.free_questions_remaining).toBe(0);

  // Run the answer through the Answers delivery endpoint (mounted on the
  // dormant router) — grounded → captured, no Stripe, no CaseCredit.
  const answerRes = await request.post(
    `${E2E_API_URL}/v1/billing/questions/purchases/${start.purchase_id}/answer`,
    { headers: { "X-Test-User-Id": userId } },
  );
  expect(answerRes.status(), await answerRes.text()).toBe(200);
  // ABS-343: POST returns `generating` and the engine settles off the
  // request path — poll until the grounded answer is captured.
  expect((await answerRes.json()).status).toBe("generating");
  const answer = await pollUntilCaptured(request, userId, start.purchase_id);
  expect(answer.answer).toBeTruthy();
  expect(answer.refinements_remaining).toBe(3);

  // The Conversation ledger is untouched: no tier credit reserved/consumed.
  const me = (await billingMe(request, userId)) as {
    free_questions_remaining: number;
    tier_balances: { reserved: number; consumed: number }[];
  };
  expect(me.free_questions_remaining).toBe(0);
  const reserved = me.tier_balances.reduce((s, b) => s + b.reserved, 0);
  const consumed = me.tier_balances.reduce((s, b) => s + b.consumed, 0);
  expect(reserved, "no CaseCredit may be reserved by the Answers path").toBe(0);
  expect(consumed, "no CaseCredit may be consumed by the Answers path").toBe(0);

  // And no Case was opened for the subject address.
  const matchRes = await request.get(
    `${E2E_API_URL}/v1/cases/match?anchor_label=${encodeURIComponent(
      VALID_INPUTS.address,
    )}&anchor_kind=address`,
    { headers: { "X-Test-User-Id": userId } },
  );
  expect(matchRes.status(), await matchRes.text()).toBe(200);
  const match = (await matchRes.json()) as { case: unknown | null };
  expect(match.case, "the Answers path must not open a Case").toBeNull();

  // A refinement is served free under the same purchase — no extra credit
  // and still no CaseCredit movement.
  const refineRes = await request.post(
    `${E2E_API_URL}/v1/billing/questions/purchases/${start.purchase_id}/refine`,
    {
      headers: { "X-Test-User-Id": userId },
      data: { message: "Summarize the key constraints in three bullets." },
    },
  );
  expect(refineRes.status(), await refineRes.text()).toBe(200);
  const refined = (await refineRes.json()) as { refinement_count: number };
  expect(refined.refinement_count).toBe(1);

  const meAfter = (await billingMe(request, userId)) as {
    free_questions_remaining: number;
  };
  expect(meAfter.free_questions_remaining).toBe(0);
});

test("decoupled: the answer view renders the grounded answer (real backend, no stubs)", async ({
  page,
  request,
}) => {
  // Create the purchase for the demo user (the browser is authenticated as
  // DEMO_USER_ID) so the answer view — which loads the purchase by owner —
  // resolves it. Drives the real /app/answers/{id} surface end to end.
  // Grant a credit first so this doesn't depend on the shared demo user's
  // remaining free-question balance.
  const grantRes = await request.post(
    `${E2E_API_URL}/v1/_test/buy-answer/grant-free-questions`,
    { data: { user_id: DEMO_USER_ID, quantity: 1 } },
  );
  expect(grantRes.status(), await grantRes.text()).toBe(200);

  const startRes = await freeStart(request, DEMO_USER_ID, VALID_INPUTS);
  expect(startRes.status(), await startRes.text()).toBe(200);
  const { purchase_id } = (await startRes.json()) as { purchase_id: number };

  await page.goto(`/app/answers/${purchase_id}`);

  // The view auto-runs the answer (POST .../answer) then renders it.
  await expect(page.getByTestId("answer-body")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("answer-body")).not.toBeEmpty();
  await expect(page.getByTestId("paid-answer-disclaimer")).toBeVisible();
  await expect(page.getByTestId("refine-remaining")).toContainText(
    /FOLLOW-UPS LEFT/i,
  );
});
