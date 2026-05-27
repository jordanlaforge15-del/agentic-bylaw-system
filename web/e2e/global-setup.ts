// Playwright globalSetup: re-mint a fresh batch of credits for the
// demo user before each run, then verify the FastAPI test server is
// reachable. The case-credit model burns one credit per case open and
// only refunds on settlement, so a long suite drains the seeded budget
// quickly. Re-seeding via the shell script (which is idempotent and
// caps at the requested credit count) keeps the user topped up.

import { execSync } from "node:child_process";
import * as path from "node:path";

export default async function globalSetup() {
  const repoRoot = path.resolve(__dirname, "..", "..");
  const seed = path.join(repoRoot, "scripts", "seed_e2e_user.py");
  const venvPython = path.join(repoRoot, ".venv", "bin", "python");
  const databaseUrl =
    process.env.DATABASE_URL ||
    "postgresql+psycopg://layer1:layer1@localhost:5432/layer1_test";

  try {
    execSync(`"${venvPython}" "${seed}" --credits-per-tier 200`, {
      env: {
        ...process.env,
        DATABASE_URL: databaseUrl,
        PYTHONPATH: `${path.join(repoRoot, "src")}:${process.env.PYTHONPATH || ""}`,
      },
      stdio: "inherit",
    });
  } catch (err) {
    console.error("globalSetup: seed_e2e_user.py failed", err);
    throw err;
  }

  // ABS-53: generate the synthetic IFC fixture the submission-upload
  // spec posts. Idempotent; runs cheap. Writing it here (rather than in
  // the spec itself) keeps the spec free of subprocess machinery and
  // means a stale fixture from a prior run can't poison the upload.
  const ifcSeed = path.join(repoRoot, "scripts", "seed_e2e_submission_ifc.py");
  try {
    execSync(`"${venvPython}" "${ifcSeed}"`, {
      env: {
        ...process.env,
        DATABASE_URL: databaseUrl,
        PYTHONPATH: `${path.join(repoRoot, "src")}:${process.env.PYTHONPATH || ""}`,
      },
      stdio: "inherit",
    });
  } catch (err) {
    console.error("globalSetup: seed_e2e_submission_ifc.py failed", err);
    throw err;
  }

  // ABS-57: generate the synthetic PDF fixture the pdf-submission-confirm
  // spec needs. Only writes the file; DB seeding happens in the spec's
  // beforeAll via the seed script.
  const pdfSeed = path.join(repoRoot, "scripts", "seed_e2e_submission_pdf.py");
  try {
    execSync(`"${venvPython}" "${pdfSeed}" --skip-db`, {
      env: {
        ...process.env,
        DATABASE_URL: databaseUrl,
        PYTHONPATH: `${path.join(repoRoot, "src")}:${process.env.PYTHONPATH || ""}`,
      },
      stdio: "inherit",
    });
  } catch (err) {
    console.error("globalSetup: seed_e2e_submission_pdf.py failed", err);
    throw err;
  }

  const apiUrl = process.env.E2E_API_URL || "http://127.0.0.1:8001";
  const res = await fetch(`${apiUrl}/healthz`).catch(() => null);
  if (!res || !res.ok) {
    throw new Error(
      `globalSetup: FastAPI test server not reachable at ${apiUrl}/healthz. ` +
        "Run scripts/e2e-up.sh before invoking playwright.",
    );
  }
}
