// Functional: ABS-332 — a grounded answer carrying a NUL byte must not 500.
//
// Reported symptom: open a case at /cases/new, pick a question, click "Get
// answer", wait through "Generating your answer…", then a raw HTTP 500
// ("Couldn't run the answer (HTTP 500)").
//
// Root cause: real bylaw evidence is extracted from municipal PDFs that
// carry stray NUL (0x00) bytes. A NUL rides untouched through the engine
// into the persisted answer — answer_text (Postgres `text`) and
// transcript_json (`jsonb`) — both written by db.flush() OUTSIDE
// run_answer's engine-error guard. Postgres rejects the NUL
// ("text fields cannot contain NUL" / "\u0000 cannot be converted to
// text"), and the DataError escapes as a 500 on a grounded, paid-for
// answer. SQLite + the mock gateway accept the byte, so this only shows up
// against the real Postgres-backed stack — exactly what the e2e suite runs.
//
// The MOCK_NUL_BYTES sentinel makes the mock gateway ground with a NUL in
// the tool_use input (→ transcript JSONB) and answer with a NUL in the
// final text (→ answer_text TEXT), reproducing both rejected writes through
// the real Next-proxy ↔ FastAPI ↔ Postgres chain.

import { execSync } from "node:child_process";
import * as path from "node:path";

import { DEMO_USER_ID, E2E_API_URL, expect, test } from "../fixtures/test-env";
import { resolveDatabaseUrl } from "../helpers/database-url";

type Req = import("@playwright/test").APIRequestContext;

// proposed_use carries the sentinel so the rendered answer prompt trips the
// mock dispatcher's NUL-injection branch.
const NUL_INPUTS = {
  address: "1234 Elm St, Halifax",
  proposed_use: "a four-unit dwelling MOCK_NUL_BYTES",
};

function makeUserId(suffix: string): string {
  return `abs332-${suffix}-${Date.now()}-${Math.random()
    .toString(36)
    .slice(2, 8)}`;
}

// ABS-343: POST /answer dispatches a background generation job and returns
// `generating`; poll the purchase until the job settles to `captured`, then
// return the settled body.
async function pollUntilCaptured(
  request: Req,
  userId: string,
  purchaseId: number,
): Promise<{ status: string; answer: string | null }> {
  let last: { status: string; answer: string | null } = {
    status: "unknown",
    answer: null,
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

function seedUser(userId: string, freeQuestions: number): void {
  const repoRoot = path.resolve(__dirname, "..", "..", "..");
  const seed = path.join(repoRoot, "scripts", "seed_e2e_user.py");
  const venvPython = path.join(repoRoot, ".venv", "bin", "python");
  const databaseUrl = resolveDatabaseUrl();
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

test("a NUL byte in a grounded answer is scrubbed, not 500'd (real Postgres)", async ({
  request,
}) => {
  const userId = makeUserId("api");
  seedUser(userId, 1);

  const startRes = await freeStart(request, userId, NUL_INPUTS);
  expect(startRes.status(), await startRes.text()).toBe(200);
  const { purchase_id } = (await startRes.json()) as { purchase_id: number };

  // The poisoned run settles to a clean, persisted answer — a 500 here is
  // the regression. Postgres would reject the NUL on db.flush() without the
  // _scrub_text / _json_safe sanitizers.
  const answerRes = await request.post(
    `${E2E_API_URL}/v1/billing/questions/purchases/${purchase_id}/answer`,
    { headers: { "X-Test-User-Id": userId } },
  );
  expect(answerRes.status(), await answerRes.text()).toBe(200);
  // ABS-343: the run is a background job now — poll until it settles.
  const answer = await pollUntilCaptured(request, userId, purchase_id);
  expect(answer.answer).toBeTruthy();
  // The scrubbed NUL leaves the surrounding text intact.
  expect(answer.answer).not.toContain("\u0000");
  expect(answer.answer).toContain("permitted use is confirmed");
});

test("the answer view renders a NUL-carrying grounded answer end to end", async ({
  page,
  request,
}) => {
  // Drive the full browser → Next proxy → FastAPI → Postgres chain: the
  // answer view auto-runs POST .../answer, which persists the NUL-bearing
  // transcript + answer_text. Without the fix this paints the HTTP-500
  // notice from the screenshot instead of the answer body.
  const grantRes = await request.post(
    `${E2E_API_URL}/v1/_test/buy-answer/grant-free-questions`,
    { data: { user_id: DEMO_USER_ID, quantity: 1 } },
  );
  expect(grantRes.status(), await grantRes.text()).toBe(200);

  const startRes = await freeStart(request, DEMO_USER_ID, NUL_INPUTS);
  expect(startRes.status(), await startRes.text()).toBe(200);
  const { purchase_id } = (await startRes.json()) as { purchase_id: number };

  await page.goto(`/app/answers/${purchase_id}`);

  await expect(page.getByTestId("answer-body")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("answer-body")).not.toBeEmpty();
  await expect(page.getByTestId("paid-answer-disclaimer")).toBeVisible();
});
