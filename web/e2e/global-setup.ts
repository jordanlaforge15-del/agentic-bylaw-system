// Playwright globalSetup: re-mint a fresh batch of credits for the
// demo user before each run, then verify the FastAPI test server is
// reachable. The case-credit model burns one credit per case open and
// only refunds on settlement, so a long suite drains the seeded budget
// quickly. Re-seeding via the shell script (which is idempotent and
// caps at the requested credit count) keeps the user topped up.

import { execSync } from "node:child_process";
import * as path from "node:path";
import { resolveDatabaseUrl } from "./helpers/database-url";

export default async function globalSetup() {
  const repoRoot = path.resolve(__dirname, "..", "..");
  const seed = path.join(repoRoot, "scripts", "seed_e2e_user.py");
  const venvPython = path.join(repoRoot, ".venv", "bin", "python");
  // ABS-207 / ABS-501: honor PG_PORT so seeds land in the right Postgres
  // when a worktree overrides ports for parallel `make e2e`. The
  // shell-level DATABASE_URL exported by scripts/e2e-up.sh doesn't
  // survive the make recipe's per-line subshell, and an *inherited* one
  // may be a stale export naming a torn-down port — so PG_PORT wins on
  // disagreement. See the helper for the full precedence rule.
  const databaseUrl = resolveDatabaseUrl();

  try {
    execSync(`"${venvPython}" "${seed}" --credits-per-tier 200 --free-questions 3`, {
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

  await warmRoutes();
}

// ABS-460: compile the heavy client routes before the first test's clock
// starts.
//
// e2e-up.sh waits for the Next dev server to answer, but Turbopack compiles a
// route on its FIRST request — /app and /cases/new cost 10-20s cold. That bill
// currently lands on whichever spec happens to reach them first, and with four
// workers it lands on several at once: an isolated run of the three
// sidebar specs failed 4/4 against a freshly-booted stack and passed 3/4
// against the same stack once warm. Nothing about those specs changed; only
// whether someone else had already paid for the compile. Warming here moves
// the cost into setup, where it is not on any assertion's timeout.
//
// Best-effort throughout: a failure to warm is not a reason to fail the run,
// it just means the first test pays what it used to.
async function warmRoutes(): Promise<void> {
  const baseUrl = process.env.E2E_BASE_URL || "http://localhost:3001";
  const demoPassword = process.env.E2E_DEMO_PASSWORD || "e2e-demo-pw";

  // /app and /cases/new sit behind proxy.ts's password gate; without the
  // cookie the warm-up would compile /access instead of the routes we care
  // about.
  let cookie = "";
  try {
    const gate = await fetch(`${baseUrl}/api/access`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ gate: "demo", password: demoPassword }),
    });
    cookie = gate.headers
      .getSetCookie()
      .map((c) => c.split(";")[0])
      .join("; ");
  } catch {
    // No gate cookie — the gated routes below will only warm the redirect.
  }

  const routes = ["/", "/pricing", "/cases/new", "/cases", "/app", "/billing"];
  const started = Date.now();
  // Serially: four parallel cold compiles is the pile-up we are trying to
  // avoid, and setup is not on any test's clock.
  for (const route of routes) {
    try {
      const r = await fetch(`${baseUrl}${route}`, {
        headers: cookie ? { cookie } : {},
      });
      // Drain the body so the render actually completes rather than being
      // abandoned at the first byte.
      await r.text();
    } catch {
      // Best effort — see above.
    }
  }
  console.log(
    `globalSetup: warmed ${routes.length} routes in ${Date.now() - started}ms`,
  );
}
