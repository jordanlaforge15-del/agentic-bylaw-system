// Functional: ABS-384 — per-report gates (ADVISOR_ENABLED_QUESTIONS).
//
// Each of the five report slugs is independently enable/disableable at
// request time via ADVISOR_ENABLED_QUESTIONS (csv slugs; `*` = all;
// unset/empty = none). Disabled slugs vanish from the priced-question menu
// and reject new purchases server-side; already-purchased reports stay
// accessible (covered in purchased-report-disabled-slug.spec.ts).
//
// The e2e stack boots FastAPI with ADVISOR_ENABLED_QUESTIONS='*' (see
// scripts/e2e-up.sh), so the REAL proxied menu returns all five launch
// slugs — the "all" case is asserted against the live backend through the
// Next proxy. The "one" and "zero" subsets are driven by stubbing the
// proxied menu response (the server env is fixed for the run), proving the
// case-open menu faithfully renders whatever subset the gate advertises.

import { E2E_API_URL, expect, test } from "../fixtures/test-env";

const LAUNCH_SLUGS = [
  "permitted_use",
  "development_standards",
  "due_diligence",
  "legal_nonconforming",
  "variance_justification",
];

test.describe("per-report gate matrix (ABS-384)", () => {
  test("all: the real proxied menu advertises every enabled launch slug", async ({
    page,
  }) => {
    // Hit the real backend through the Next proxy. The e2e stack enables
    // all slugs, so the gate returns the full launch catalog.
    const res = await page.request.get("/api/billing/questions");
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    const slugs = (body.questions as Array<{ slug: string }>).map(
      (q) => q.slug,
    );
    for (const slug of LAUNCH_SLUGS) {
      expect(slugs).toContain(slug);
    }
    // Envelope fields present regardless of subset.
    expect(body).toHaveProperty("enabled");
    expect(body).toHaveProperty("conversation_enabled");
  });

  test("one: a single-slug gate renders exactly that menu item", async ({
    page,
  }) => {
    await page.route("**/api/billing/questions", async (route) => {
      const resp = await route.fetch();
      const body = await resp.json();
      body.questions = (
        body.questions as Array<{ slug: string }>
      ).filter((q) => q.slug === "permitted_use");
      await route.fulfill({ response: resp, json: body });
    });

    await page.goto("/cases/new");
    await expect(page.getByTestId("question-menu")).toBeVisible();
    await expect(
      page.getByTestId("question-option-permitted_use"),
    ).toBeVisible();
    // The other four slugs are absent from the menu.
    for (const slug of LAUNCH_SLUGS.filter((s) => s !== "permitted_use")) {
      await expect(
        page.getByTestId(`question-option-${slug}`),
      ).toHaveCount(0);
    }
  });

  test("zero: an empty gate renders no report section at all", async ({
    page,
  }) => {
    await page.route("**/api/billing/questions", async (route) => {
      // The zero-report assertions all check for *absence*, so they resolve
      // before this real round-trip completes and the page can close
      // mid-fetch. Swallow the "page closed" rejection instead of failing.
      try {
        const resp = await route.fetch();
        const body = await resp.json();
        body.questions = [];
        await route.fulfill({ response: resp, json: body });
      } catch {
        /* page closed before the menu fetch settled — nothing to fulfill */
      }
    });

    await page.goto("/cases/new");
    // ABS-385: with zero enabled slugs the report accordion is not rendered
    // at all — the page reads complete as anchor + question + free CTA. The
    // menu container and every option are absent from the DOM.
    await expect(page.getByTestId("start-conversation-btn")).toBeVisible();
    await expect(page.getByTestId("question-menu")).toHaveCount(0);
    await expect(page.getByText(/OR ORDER A WRITTEN REPORT/)).toHaveCount(0);
    for (const slug of LAUNCH_SLUGS) {
      await expect(
        page.getByTestId(`question-option-${slug}`),
      ).toHaveCount(0);
    }
  });
});

// ABS-387: the same gate drives the /pricing "Written reports" section. The
// pricing page fetches /api/billing/questions client-side, so the gate subset
// is stubbable the same way — only the enabled slugs become ReportSku cards.
test.describe("pricing reports section reflects the gate (ABS-387)", () => {
  test("all: every enabled slug renders a ReportSku card", async ({ page }) => {
    await page.goto("/pricing");
    await expect(page.getByTestId("reports-section")).toContainText(
      /WRITTEN REPORTS · 5 AVAILABLE/i,
    );
    for (const slug of LAUNCH_SLUGS) {
      await expect(page.getByTestId(`report-sku-${slug}`)).toBeVisible();
    }
  });

  test("one: a single-slug gate renders exactly one ReportSku card", async ({
    page,
  }) => {
    await page.route("**/api/billing/questions", async (route) => {
      const resp = await route.fetch();
      const body = await resp.json();
      body.questions = (
        body.questions as Array<{ slug: string }>
      ).filter((q) => q.slug === "permitted_use");
      await route.fulfill({ response: resp, json: body });
    });

    await page.goto("/pricing");
    await expect(page.getByTestId("reports-section")).toContainText(
      /WRITTEN REPORTS · 1 AVAILABLE/i,
    );
    await expect(page.getByTestId("report-sku-permitted_use")).toBeVisible();
    for (const slug of LAUNCH_SLUGS.filter((s) => s !== "permitted_use")) {
      await expect(page.getByTestId(`report-sku-${slug}`)).toHaveCount(0);
    }
  });

  test("zero: an empty gate drops the reports section entirely", async ({
    page,
  }) => {
    await page.route("**/api/billing/questions", async (route) => {
      const resp = await route.fetch();
      const body = await resp.json();
      body.questions = [];
      await route.fulfill({ response: resp, json: body });
    });

    await page.goto("/pricing");
    // Trial + top-ups still render; the reports section is gone.
    await expect(page.getByTestId("trial-card")).toBeVisible();
    await expect(page.getByTestId("reports-section")).toHaveCount(0);
    await expect(page.getByTestId("report-contact-card")).toBeVisible();
  });
});

// ABS-390 (folded in from the retired ABS-324 spec): a report purchase is
// DECOUPLED from the conversation product. Ordering a gated report (free-start
// in the payments-off posture) opens an Answers `QuestionPurchase`, NOT a Case,
// and never reserves or consumes a CaseCredit from the conversation ledger.
// This is the decoupling that lets a report slug be gated independently of the
// turn-based chat. Drives the REAL dormant billing router (no stubs).
test.describe("report purchase is decoupled from the conversation ledger (ABS-390)", () => {
  const VALID_INPUTS = {
    address: "1234 Elm St, Halifax",
    proposed_use: "a four-unit dwelling",
  };

  const sumLedger = (
    balances: { reserved: number; consumed: number }[],
  ): { reserved: number; consumed: number } => ({
    reserved: balances.reduce((s, b) => s + b.reserved, 0),
    consumed: balances.reduce((s, b) => s + b.consumed, 0),
  });

  test("free-start opens an Answers purchase with zero CaseCredit and no Case", async ({
    request,
  }) => {
    const userId = `abs390dec-${Date.now()}-${Math.random()
      .toString(36)
      .slice(2, 8)}`;

    // Establish the pre-flight baseline via /me. The first authenticated
    // request auto-provisions the user and issues the one-time signup starter
    // grant (FREE_QUESTION_GRANT free questions), so reading /me first both
    // settles that grant and pins the free-question count we expect free-start
    // to decrement by exactly one. It also confirms the conversation ledger
    // starts clean (no CaseCredit reserved/consumed) — that ledger is separate
    // from the free-question entitlement the Answers path draws on.
    const beforeRes = await request.get(`${E2E_API_URL}/v1/billing/me`, {
      headers: { "X-Test-User-Id": userId },
    });
    expect(beforeRes.status(), await beforeRes.text()).toBe(200);
    const before = (await beforeRes.json()) as {
      tier_balances: { reserved: number; consumed: number }[];
      free_questions_remaining: number;
    };
    const freeBefore = before.free_questions_remaining;
    expect(freeBefore, "the signup starter grant seeds free questions").toBeGreaterThan(
      0,
    );
    const beforeLedger = sumLedger(before.tier_balances);
    expect(beforeLedger.reserved).toBe(0);
    expect(beforeLedger.consumed).toBe(0);

    // free-start unlocks the report → an Answers QuestionPurchase, not a Case.
    const startRes = await request.post(
      `${E2E_API_URL}/v1/billing/questions/free-start`,
      {
        headers: { "X-Test-User-Id": userId },
        data: {
          question_slug: "permitted_use",
          inputs: VALID_INPUTS,
          anchor_label: VALID_INPUTS.address,
          anchor_kind: "address",
        },
      },
    );
    expect(startRes.status(), await startRes.text()).toBe(200);
    const start = (await startRes.json()) as {
      purchase_id: number;
      status: string;
      case_id?: number;
      free_questions_remaining: number;
    };
    expect(start.purchase_id).toBeGreaterThan(0);
    expect(start.status).toBe("authorized");
    // The decoupling: no Case identifier is handed back.
    expect(start.case_id).toBeUndefined();
    // Exactly one free question is drawn from the Answers ledger — not zeroed,
    // not more — proving the report unlock spends its own entitlement.
    expect(start.free_questions_remaining).toBe(freeBefore - 1);

    // The conversation ledger is untouched: no CaseCredit reserved/consumed.
    const meRes = await request.get(`${E2E_API_URL}/v1/billing/me`, {
      headers: { "X-Test-User-Id": userId },
    });
    expect(meRes.status(), await meRes.text()).toBe(200);
    const me = (await meRes.json()) as {
      tier_balances: { reserved: number; consumed: number }[];
    };
    const afterLedger = sumLedger(me.tier_balances);
    expect(afterLedger.reserved, "the Answers path reserves no CaseCredit").toBe(0);
    expect(afterLedger.consumed, "the Answers path consumes no CaseCredit").toBe(0);

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
  });
});
