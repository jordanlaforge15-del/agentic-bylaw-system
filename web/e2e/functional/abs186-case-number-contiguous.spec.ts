// Functional: user_case_number is always 1-based and contiguous, even
// when the global advisor_case.id has gaps from rolled-back transactions.
//
// Regression for ABS-186: using the raw Postgres sequence id for the
// user-visible "Case #N" label caused gaps (e.g. #1, #5, #7) when
// earlier transactions allocated sequence values then rolled back.
// The fix assigns user_case_number = MAX(user_case_number) + 1 inside
// the same transaction as the case row, so it always rolls back with
// the case on failure.

import { execSync } from "node:child_process";
import * as path from "node:path";

import {
  DEMO_USER_ID,
  E2E_API_URL,
  expect,
  test,
} from "../fixtures/test-env";

type OpenCaseBody = {
  case: {
    id: number;
    user_case_number: number;
  };
  credit_id: number;
  reused_existing_case: boolean;
};

async function openCase(anchorLabel: string, userId: string): Promise<OpenCaseBody> {
  const res = await fetch(`${E2E_API_URL}/v1/cases`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Test-User-Id": userId,
    },
    body: JSON.stringify({
      anchor_label: anchorLabel,
      anchor_kind: "address",
      tier: "standard",
    }),
  });
  if (!res.ok) {
    throw new Error(`openCase failed: ${res.status} ${await res.text()}`);
  }
  return (await res.json()) as OpenCaseBody;
}

// Unique user per run so parallel workers opening cases for DEMO_USER_ID
// don't interleave and break the +1 sequential assertion.
const SEQ_USER_ID = `abs186-seq-${Date.now()}-${Math.random()
  .toString(36)
  .slice(2, 7)}`;

test.beforeAll(() => {
  const repoRoot = path.resolve(__dirname, "..", "..", "..");
  const seed = path.join(repoRoot, "scripts", "seed_e2e_user.py");
  const venvPython = path.join(repoRoot, ".venv", "bin", "python");
  const pgPort = process.env.PG_PORT || "5432";
  const databaseUrl =
    process.env.DATABASE_URL ||
    `postgresql+psycopg://layer1:layer1@localhost:${pgPort}/layer1_test`;
  execSync(
    `"${venvPython}" "${seed}" --user-id "${SEQ_USER_ID}" ` +
      `--email "${SEQ_USER_ID}@e2e.test" --credits-per-tier 5`,
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
});

test("user_case_number is present and sequential across two new cases", async () => {
  const suffix = `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
  const first = await openCase(`abs186-first-${suffix}`, SEQ_USER_ID);
  const second = await openCase(`abs186-second-${suffix}`, SEQ_USER_ID);

  // user_case_number must be a positive integer on each response.
  expect(first.case.user_case_number).toBeGreaterThan(0);
  expect(second.case.user_case_number).toBeGreaterThan(0);

  // The second case number must be exactly one more than the first —
  // no gaps, regardless of what happened to advisor_case.id.
  expect(second.case.user_case_number).toBe(first.case.user_case_number + 1);

  // The two cases must have different DB ids (they are distinct cases).
  expect(second.case.id).not.toBe(first.case.id);
});

test("Case #N label in /app header shows user_case_number, not raw DB id", async ({
  page,
}) => {
  // Open a case via the API and navigate to /app with both case_id and
  // case_number in the URL (the same redirect the case-open form uses).
  const suffix = `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
  const opened = await openCase(`abs186-label-${suffix}`, DEMO_USER_ID);
  const { id: caseId, user_case_number: caseNum } = opened.case;

  await page.goto(`/app?case_id=${caseId}&case_number=${caseNum}`);

  // The header strip must show "CASE #<caseNum>" — the user-scoped
  // sequential number — not the raw database id.
  await expect(
    page.getByText(new RegExp(`CASE #${caseNum}`, "i")),
  ).toBeVisible({ timeout: 10_000 });
});
