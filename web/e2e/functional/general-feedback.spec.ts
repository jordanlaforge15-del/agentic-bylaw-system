// ABS-129 — General feedback form (non-message-specific).
//
// Verifies:
//  1. The feedback button is visible in the app header.
//  2. Clicking it opens the feedback form sheet.
//  3. Submitting feedback with a category and body succeeds (API returns 200).
//  4. The success confirmation is shown after submission.
//  5. The API rejects invalid payloads (missing body, invalid category).

import { expect, test } from "../fixtures/test-env";

const E2E_API_URL = process.env.E2E_API_URL || "http://127.0.0.1:8001";
const DEMO_USER_ID = process.env.E2E_USER_ID || "demo-user-1";

function freshUserId(label: string): string {
  return `e2e-feedback-${label}-${Date.now()}-${Math.random()
    .toString(36)
    .slice(2, 8)}`;
}

async function backend(
  path: string,
  init: {
    method?: "GET" | "POST";
    body?: unknown;
    userId?: string;
  } = {},
): Promise<Response> {
  return fetch(`${E2E_API_URL}${path}`, {
    method: init.method ?? "GET",
    headers: {
      "Content-Type": "application/json",
      "X-Test-User-Id": init.userId ?? DEMO_USER_ID,
    },
    body: init.body !== undefined ? JSON.stringify(init.body) : undefined,
  });
}

test.describe("general feedback form", () => {
  test("feedback button opens the form, submit succeeds, shows confirmation", async ({
    page,
  }) => {
    await page.goto("/app");

    // The feedback trigger button should be visible
    const trigger = page.locator("[data-testid='feedback-trigger']");
    await expect(trigger).toBeVisible();

    // Click opens the sheet
    await trigger.click();
    const categorySelect = page.locator("[data-testid='feedback-category']");
    await expect(categorySelect).toBeVisible();

    // Fill the form
    await categorySelect.selectOption("feature_request");
    const bodyInput = page.locator("[data-testid='feedback-body']");
    await bodyInput.fill("I would love a dark mode toggle in the sidebar");

    // Submit
    await page.getByRole("button", { name: "Submit Feedback" }).click();

    // Success state is shown
    const success = page.locator("[data-testid='feedback-success']");
    await expect(success).toBeVisible({ timeout: 5000 });
    await expect(success).toContainText("THANK YOU");
  });

  test("API POST /v1/feedback stores feedback and returns 200", async () => {
    const userId = freshUserId("api-submit");
    // Ensure user exists
    await backend("/v1/terms/current", { userId });

    const res = await backend("/v1/feedback", {
      method: "POST",
      userId,
      body: { category: "bug_report", body: "The map overlay flickers on zoom" },
    });
    expect(res.status).toBe(200);
    const data = (await res.json()) as {
      id: number;
      category: string;
      created_at: string;
    };
    expect(data.id).toBeGreaterThan(0);
    expect(data.category).toBe("bug_report");
    expect(Date.parse(data.created_at)).not.toBeNaN();
  });

  test("API rejects empty body with 422", async () => {
    const userId = freshUserId("empty-body");
    await backend("/v1/terms/current", { userId });

    const res = await backend("/v1/feedback", {
      method: "POST",
      userId,
      body: { category: "general", body: "" },
    });
    expect(res.status).toBe(422);
  });

  test("API rejects invalid category with 422", async () => {
    const userId = freshUserId("bad-category");
    await backend("/v1/terms/current", { userId });

    const res = await backend("/v1/feedback", {
      method: "POST",
      userId,
      body: { category: "invalid_cat", body: "some feedback" },
    });
    expect(res.status).toBe(422);
  });
});
