// Functional regression: ABS-130 — Halifax ground-truth evaluation
// end-to-end through the real-stack HTTP path.
//
// Extends the Evaluator E2E Bylaw with three additional clauses
// (rear_setback_m, side_setback_m, lot_coverage_pct) sourced from
// the ER-1 zone standards in the Halifax Regional Centre LUB
// ground-truth test set.  Values here mirror the Layer 2 eval cases
// in evals/halifax_regional_centre_eval.json.

import { execSync } from "node:child_process";
import * as path from "node:path";

import { E2E_API_URL, expect, test } from "../fixtures/test-env";


type EvaluateBylawsResponse = {
  overall_status: "compliant" | "non_compliant" | "uncertain" | "incomplete";
  evaluator_version: string;
  attribute_results: Array<{
    attribute_key: string;
    submitted_value: unknown;
    verdict: "pass" | "fail" | "uncertain";
    delta: Record<string, unknown> | null;
    applicable_clauses: Array<{
      fragment_id: number;
      citation_path: string | null;
      document_id: number;
      municipality: string;
      bylaw_name: string;
      text: string;
      verdict: "pass" | "fail" | "uncertain";
      constraint: {
        operator: string | null;
        value: unknown;
        unit: string | null;
        conditional_on: string[];
      } | null;
      delta: Record<string, unknown> | null;
    }>;
  }>;
};


const HALIFAX_PARCEL_LOCATION = {
  civic_number: "100",
  street: "Evaluator Way",
};


function runSeeds(): void {
  const repoRoot = path.resolve(__dirname, "..", "..", "..");
  const venvPython = path.join(repoRoot, ".venv", "bin", "python");
  const databaseUrl =
    process.env.DATABASE_URL ||
    "postgresql+psycopg://layer1:layer1@localhost:5432/layer1_test";
  const env = {
    ...process.env,
    DATABASE_URL: databaseUrl,
    PYTHONPATH: `${path.join(repoRoot, "src")}:${process.env.PYTHONPATH || ""}`,
  };

  // Ensure parent evaluator bylaw exists first
  execSync(
    `"${venvPython}" "${path.join(repoRoot, "scripts", "seed_e2e_evaluator_bylaws.py")}"`,
    { env, stdio: "inherit" },
  );
  // Then seed ground-truth clauses
  execSync(
    `"${venvPython}" "${path.join(repoRoot, "scripts", "seed_e2e_groundtruth_bylaws.py")}"`,
    { env, stdio: "inherit" },
  );
}


async function postEvaluate(
  request: import("@playwright/test").APIRequestContext,
  body: Record<string, unknown>,
): Promise<EvaluateBylawsResponse> {
  const response = await request.post(`${E2E_API_URL}/v1/_test/evaluate-bylaws`, {
    headers: { "Content-Type": "application/json" },
    data: body,
  });
  expect(
    response.status(),
    `evaluator endpoint failed: ${response.status()} ${await response.text()}`,
  ).toBe(200);
  return (await response.json()) as EvaluateBylawsResponse;
}


test.beforeAll(() => {
  runSeeds();
});


test("ground-truth: ER-1 rear setback compliant at 7.5 m", async ({ request }) => {
  const body = await postEvaluate(request, {
    attributes: [
      { attribute_key: "rear_setback_m", value: 8.0, unit: "m" },
    ],
    location: HALIFAX_PARCEL_LOCATION,
    document_filters: { bylaw_name: "Evaluator E2E Bylaw" },
  });

  expect(body.overall_status).toBe("compliant");
  const rear = body.attribute_results.find((r) => r.attribute_key === "rear_setback_m");
  expect(rear).toBeDefined();
  if (!rear) return;
  expect(rear.verdict).toBe("pass");
  expect(rear.applicable_clauses.length).toBeGreaterThan(0);
  expect(rear.applicable_clauses[0].citation_path).toBe("4.5.1");
});


test("ground-truth: ER-1 rear setback non-compliant below 7.5 m", async ({ request }) => {
  const body = await postEvaluate(request, {
    attributes: [
      { attribute_key: "rear_setback_m", value: 5.0, unit: "m" },
    ],
    location: HALIFAX_PARCEL_LOCATION,
    document_filters: { bylaw_name: "Evaluator E2E Bylaw" },
  });

  expect(body.overall_status).toBe("non_compliant");
  const rear = body.attribute_results.find((r) => r.attribute_key === "rear_setback_m");
  expect(rear).toBeDefined();
  if (!rear) return;
  expect(rear.verdict).toBe("fail");
});


test("ground-truth: ER-1 side setback compliant at 1.2 m", async ({ request }) => {
  const body = await postEvaluate(request, {
    attributes: [
      { attribute_key: "side_setback_m", value: 1.5, unit: "m" },
    ],
    location: HALIFAX_PARCEL_LOCATION,
    document_filters: { bylaw_name: "Evaluator E2E Bylaw" },
  });

  expect(body.overall_status).toBe("compliant");
  const side = body.attribute_results.find((r) => r.attribute_key === "side_setback_m");
  expect(side).toBeDefined();
  if (!side) return;
  expect(side.verdict).toBe("pass");
  expect(side.applicable_clauses[0].citation_path).toBe("4.6.1");
});


test("ground-truth: ER-1 lot coverage compliant at 35%", async ({ request }) => {
  const body = await postEvaluate(request, {
    attributes: [
      { attribute_key: "lot_coverage_pct", value: 30, unit: "%" },
    ],
    location: HALIFAX_PARCEL_LOCATION,
    document_filters: { bylaw_name: "Evaluator E2E Bylaw" },
  });

  expect(body.overall_status).toBe("compliant");
  const coverage = body.attribute_results.find((r) => r.attribute_key === "lot_coverage_pct");
  expect(coverage).toBeDefined();
  if (!coverage) return;
  expect(coverage.verdict).toBe("pass");
  expect(coverage.applicable_clauses[0].citation_path).toBe("4.7.1");
});


test("ground-truth: combined ER-1 attributes — all compliant", async ({ request }) => {
  const body = await postEvaluate(request, {
    attributes: [
      { attribute_key: "front_setback_m", value: 6.0, unit: "m" },
      { attribute_key: "rear_setback_m", value: 8.0, unit: "m" },
      { attribute_key: "side_setback_m", value: 1.5, unit: "m" },
      { attribute_key: "building_height_m", value: 9.0, unit: "m" },
      { attribute_key: "lot_coverage_pct", value: 30, unit: "%" },
    ],
    location: HALIFAX_PARCEL_LOCATION,
    document_filters: { bylaw_name: "Evaluator E2E Bylaw" },
  });

  expect(body.overall_status).toBe("compliant");
  const keys = new Set(body.attribute_results.map((r) => r.attribute_key));
  expect(keys).toEqual(
    new Set(["front_setback_m", "rear_setback_m", "side_setback_m", "building_height_m", "lot_coverage_pct"]),
  );

  for (const result of body.attribute_results) {
    expect(result.verdict).toBe("pass");
    expect(result.applicable_clauses.length).toBeGreaterThan(0);
    for (const clause of result.applicable_clauses) {
      expect(clause.citation_path).toBeTruthy();
    }
  }
});
