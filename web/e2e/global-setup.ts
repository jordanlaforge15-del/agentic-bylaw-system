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

  const apiUrl = process.env.E2E_API_URL || "http://127.0.0.1:8001";
  const res = await fetch(`${apiUrl}/healthz`).catch(() => null);
  if (!res || !res.ok) {
    throw new Error(
      `globalSetup: FastAPI test server not reachable at ${apiUrl}/healthz. ` +
        "Run scripts/e2e-up.sh before invoking playwright.",
    );
  }
}
