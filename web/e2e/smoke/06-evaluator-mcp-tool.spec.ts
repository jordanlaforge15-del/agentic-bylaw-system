// Smoke: ABS-46 critical-path coverage for the evaluator MCP tool.
// One assertion: a compliant submission round-trips through
// /v1/_test/evaluate-bylaws and returns overall_status=compliant
// with at least one cited clause per attribute. The functional spec
// at ``web/e2e/functional/evaluator-mcp-tool.spec.ts`` carries the
// full coverage (fail / uncertain / incomplete / no-location).

import { execSync } from "node:child_process";
import * as path from "node:path";

import { E2E_API_URL, expect, test } from "../fixtures/test-env";


test.beforeAll(() => {
  const repoRoot = path.resolve(__dirname, "..", "..", "..");
  const seed = path.join(repoRoot, "scripts", "seed_e2e_evaluator_bylaws.py");
  const venvPython = path.join(repoRoot, ".venv", "bin", "python");
  // ABS-207: honor PG_PORT so this seed lands in the right Postgres
  // when a worktree overrides ports for parallel `make e2e`.
  const pgPort = process.env.PG_PORT || "5433";
  const databaseUrl =
    process.env.DATABASE_URL ||
    `postgresql+psycopg://layer1:layer1@localhost:${pgPort}/layer1_test`;

  execSync(`"${venvPython}" "${seed}"`, {
    env: {
      ...process.env,
      DATABASE_URL: databaseUrl,
      PYTHONPATH: `${path.join(repoRoot, "src")}:${process.env.PYTHONPATH || ""}`,
    },
    stdio: "inherit",
  });
});


test("evaluator endpoint: compliant submission returns compliant + citations", async ({
  request,
}) => {
  const response = await request.post(`${E2E_API_URL}/v1/_test/evaluate-bylaws`, {
    headers: { "Content-Type": "application/json" },
    data: {
      attributes: [
        { attribute_key: "front_setback_m", value: 6.0, unit: "m" },
        { attribute_key: "building_height_m", value: 9.0, unit: "m" },
      ],
      location: { civic_number: "100", street: "Evaluator Way" },
      document_filters: { bylaw_name: "Evaluator E2E Bylaw" },
    },
  });
  expect(response.status()).toBe(200);
  const body = (await response.json()) as {
    overall_status: string;
    attribute_results: Array<{
      attribute_key: string;
      applicable_clauses: Array<{ citation_path: string | null }>;
    }>;
  };
  expect(body.overall_status).toBe("compliant");
  for (const result of body.attribute_results) {
    expect(result.applicable_clauses.length).toBeGreaterThan(0);
    expect(result.applicable_clauses[0].citation_path).toBeTruthy();
  }
});
