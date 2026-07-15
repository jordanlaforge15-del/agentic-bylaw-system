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

import { expect, test } from "../fixtures/test-env";

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

  test("zero: an empty gate renders a menu with no options", async ({
    page,
  }) => {
    await page.route("**/api/billing/questions", async (route) => {
      const resp = await route.fetch();
      const body = await resp.json();
      body.questions = [];
      await route.fulfill({ response: resp, json: body });
    });

    await page.goto("/cases/new");
    // The menu container still renders in the DOM; it simply carries no
    // options, so it collapses to zero height (Playwright reports an empty
    // container as hidden). Assert it is attached rather than visible.
    await expect(page.getByTestId("question-menu")).toBeAttached();
    for (const slug of LAUNCH_SLUGS) {
      await expect(
        page.getByTestId(`question-option-${slug}`),
      ).toHaveCount(0);
    }
  });
});
