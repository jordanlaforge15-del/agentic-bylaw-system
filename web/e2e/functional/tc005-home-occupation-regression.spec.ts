// Functional: ABS-280 — TC-005 T5 regression: home occupation in HR-2.
//
// AC4 of ABS-280 requires an automated regression that asserts the structured
// resolver's output for the TC-005 (use, zone) pairs so the behavior can't
// silently regress.
//
// The TC-005 T5 question is "Is home occupation permitted in HR-2?" The correct
// answer per Table 1A of the Regional Centre LUB is **conditional** — the HR-2
// cell carries footnote ⑮ ("Use is permitted, except within the Halifax Grain
// Elevator Special Area..."), not a blank. Pre-Phase-3 the advisor hedged
// because it couldn't read the matrix at all.
//
// AC2: a conditional cell must surface its footnote condition text.
// Grounding contract (AC3): every non-indeterminate result must carry a citation
// that identifies the source table, so the answer is independently verifiable
// via SQL against source_table_cell.metadata_json → permission_marker.

import type { APIRequestContext } from "@playwright/test";
import { execSync } from "node:child_process";
import * as path from "node:path";

import { E2E_API_URL, expect, test } from "../fixtures/test-env";
import { resolveDatabaseUrl } from "../helpers/database-url";

const BYLAW_NAME = "TC-005 ABS-280 Permitted Use By-law";

function runSeeds(): void {
  const repoRoot = path.resolve(__dirname, "..", "..", "..");
  const venvPython = path.join(repoRoot, ".venv", "bin", "python");
  const databaseUrl = resolveDatabaseUrl();
  const env = {
    ...process.env,
    DATABASE_URL: databaseUrl,
    PYTHONPATH: `${path.join(repoRoot, "src")}:${process.env.PYTHONPATH || ""}`,
  };
  execSync(
    `"${venvPython}" "${path.join(repoRoot, "scripts", "seed_e2e_tc005_permitted_use.py")}"`,
    { env, stdio: "inherit" },
  );
}

async function enrichAndGetDocumentId(
  request: APIRequestContext,
): Promise<number> {
  const response = await request.post(
    `${E2E_API_URL}/v1/_test/profile-permission-tables`,
    {
      headers: { "Content-Type": "application/json" },
      data: { bylaw_name: BYLAW_NAME },
    },
  );
  expect(
    response.status(),
    `profile-permission-tables failed: ${await response.text()}`,
  ).toBe(200);
  const body = await response.json();
  expect(body.table_count).toBeGreaterThanOrEqual(1);
  return body.document_id as number;
}

async function lookupPermittedUse(
  request: APIRequestContext,
  documentId: number,
  use: string,
  zone: string,
) {
  const response = await request.post(`${E2E_API_URL}/v1/_test/lookup-citation`, {
    headers: { "Content-Type": "application/json" },
    data: {
      structured: { kind: "permitted_use", use, zone },
      document_id: documentId,
    },
  });
  expect(
    response.status(),
    `lookup-citation failed: ${await response.text()}`,
  ).toBe(200);
  const body = await response.json();
  expect(body.permitted_use, "expected a permitted_use envelope").toBeTruthy();
  return body.permitted_use;
}

let documentId: number;

test.beforeAll(async ({ request }) => {
  runSeeds();
  documentId = await enrichAndGetDocumentId(request);
});

// ---------------------------------------------------------------------------
// AC4 + AC1/AC2/AC3: TC-005 T5 — home occupation CONDITIONAL in HR-2 (⑮)
// ---------------------------------------------------------------------------

test("TC-005 T5: home occupation is conditional in HR-2 (Table 1A footnote ⑮)", async ({
  request,
}) => {
  const result = await lookupPermittedUse(
    request,
    documentId,
    "home occupation use",
    "HR-2",
  );
  expect(result.indeterminate).toBeFalsy();
  expect(result.permission).toBe("conditional");
  // AC2: the footnote ordinal AND its condition text must be present.
  expect(result.footnote_ordinal).toBe(15);
  expect(result.condition_text, "conditional cell must cite footnote text").toBeTruthy();
  expect(result.condition_text).toContain("Halifax Grain Elevator");
  // AC3: grounded in a Table 1A citation.
  expect(result.citation).toBeTruthy();
  expect(result.citation.bylaw_name).toBe(BYLAW_NAME);
});

test("TC-005 T5: bare use form ('home occupation') resolves the same conditional cell", async ({
  request,
}) => {
  // ABS-282 use-form tolerance — no trailing "use".
  const result = await lookupPermittedUse(
    request,
    documentId,
    "home occupation",
    "HR-2",
  );
  expect(result.indeterminate).toBeFalsy();
  expect(result.permission).toBe("conditional");
  expect(result.footnote_ordinal).toBe(15);
});

// ---------------------------------------------------------------------------
// AC4 + AC1/AC3: TC-005 T3 — multi-unit dwelling IS permitted in HR-2
// ---------------------------------------------------------------------------

test("TC-005 T3: multi-unit dwelling is permitted in HR-2 (Table 1A ● cell)", async ({
  request,
}) => {
  const result = await lookupPermittedUse(
    request,
    documentId,
    "multi-unit dwelling use",
    "HR-2",
  );
  expect(result.indeterminate).toBeFalsy();
  expect(result.permission).toBe("permitted");
  // AC3: grounded citation must be present.
  expect(result.citation).toBeTruthy();
  expect(result.citation.bylaw_name).toBe(BYLAW_NAME);
});

// ---------------------------------------------------------------------------
// Zone-specificity: home occupation permitted in DD — resolver is zone-aware
// ---------------------------------------------------------------------------

test("zone contrast: home occupation is permitted in DD but conditional in HR-2", async ({
  request,
}) => {
  const dd = await lookupPermittedUse(
    request,
    documentId,
    "home occupation use",
    "DD",
  );
  expect(dd.permission).toBe("permitted");
  // The bare ● cell carries no footnote.
  expect(dd.footnote_ordinal ?? null).toBeNull();

  const hr2 = await lookupPermittedUse(
    request,
    documentId,
    "home occupation use",
    "HR-2",
  );
  expect(hr2.permission).toBe("conditional");

  // The two results must differ — if both return the same verdict, the resolver
  // is ignoring the zone argument and both AC4 and zone-awareness are broken.
  expect(dd.permission).not.toBe(hr2.permission);
});
