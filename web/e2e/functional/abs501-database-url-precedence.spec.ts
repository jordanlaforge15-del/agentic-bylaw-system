// ABS-501: a stale inherited DATABASE_URL must never outrank the live
// stack's PG_PORT.
//
// scripts/e2e-up.sh and the Night Manager's agent runner both export a
// DATABASE_URL pinned to the port of the stack they just booted, and that
// export survives teardown in the surrounding shell. Every seed path used to
// prefer it over PG_PORT, so the next run seeded a dead port while FastAPI
// queried the live one — six phantom Postgres failures on a branch that was
// green the whole time (Data Model 3.0 post-mortem; ~$17 of agent time).
//
// Unit tests pin the resolver in isolation
// (tests/test_abs501_database_url_precedence.py). This spec pins it against
// the *running* stack, which is the only place the two halves meet:
//
//   test 1-3 -> the TypeScript resolver every spec/globalSetup now calls:
//               conflict, agreement, and no-PG_PORT.
//   test 4   -> a Python child process handed a dead-port DATABASE_URL
//               resolves settings at PG_PORT and actually queries the live
//               database (the pytest failure signature, reproduced).
//   test 5   -> a real seed script run with the same poisoned environment
//               succeeds — the globalSetup path end to end.
//   test 6   -> agreement stays silent: no false alarm, no rewrite.

import { execFileSync } from "node:child_process";
import * as path from "node:path";

import { expect, test } from "../fixtures/test-env";
import {
  E2E_PG_PORT_DEFAULT,
  reconcileDatabaseUrl,
  resolveDatabaseUrl,
} from "../helpers/database-url";

const repoRoot = path.resolve(__dirname, "..", "..", "..");
const venvPython = path.join(repoRoot, ".venv", "bin", "python");

/** The port the stack under test is actually on. */
const livePort = process.env.PG_PORT || E2E_PG_PORT_DEFAULT;
/** A port nothing is listening on — the shape of a torn-down stack's export. */
const deadPort = "5599";
const stale = `postgresql+psycopg://layer1:layer1@localhost:${deadPort}/layer1_test`;

/** Environment of a shell that still carries a dead stack's DATABASE_URL. */
function poisonedEnv(): NodeJS.ProcessEnv {
  return {
    ...process.env,
    DATABASE_URL: stale,
    PG_PORT: livePort,
    PYTHONPATH: `${path.join(repoRoot, "src")}:${path.join(repoRoot, "scripts")}`,
  };
}

test.describe("ABS-501 DATABASE_URL / PG_PORT precedence", () => {
  test("PG_PORT wins over a stale DATABASE_URL, loudly", () => {
    const { url, warning } = reconcileDatabaseUrl({
      DATABASE_URL: stale,
      PG_PORT: livePort,
    });

    expect(url).toBe(
      `postgresql+psycopg://layer1:layer1@localhost:${livePort}/layer1_test`,
    );
    // Actionable: names the port that lost, the one that won, and the fix.
    expect(warning).toContain(deadPort);
    expect(warning).toContain(`PG_PORT=${livePort}`);
    expect(warning).toContain("unset DATABASE_URL");
  });

  test("no false alarm when DATABASE_URL and PG_PORT agree", () => {
    const agreeing = `postgresql+psycopg://layer1:layer1@localhost:${livePort}/layer1_test`;
    const { url, warning } = reconcileDatabaseUrl({
      DATABASE_URL: agreeing,
      PG_PORT: livePort,
    });

    expect(url).toBe(agreeing);
    expect(warning).toBeUndefined();
  });

  test("without PG_PORT there is nothing to disagree with", () => {
    const { url, warning } = reconcileDatabaseUrl({ DATABASE_URL: stale });
    expect(url).toBe(stale);
    expect(warning).toBeUndefined();
  });

  test("a Python child with a dead-port DATABASE_URL queries the live DB", () => {
    // This is the exact repro from the post-mortem: the same module that
    // returned "3 failed" under a stale DATABASE_URL resolves settings via
    // layer1.config, which now reconciles first. Selecting 1 proves the
    // resolved URL is not merely rewritten but reachable.
    const probe =
      "from layer1.config import get_settings;" +
      "from sqlalchemy import create_engine, text;" +
      "url = get_settings().database_url;" +
      "conn = create_engine(url).connect();" +
      "conn.execute(text('SELECT 1'));" +
      "conn.close();" +
      "print(url)";

    const out = execFileSync(venvPython, ["-c", probe], {
      cwd: repoRoot,
      env: poisonedEnv(),
      encoding: "utf-8",
    }).trim();

    expect(out.split("\n").pop()).toContain(`localhost:${livePort}/`);
    expect(out).not.toContain(`:${deadPort}/`);
  });

  test("a seed script run from a poisoned shell still succeeds", () => {
    // globalSetup's own first seed. Under the old resolution this exits
    // non-zero with connection-refused against the dead port.
    const seed = path.join(repoRoot, "scripts", "seed_e2e_user.py");
    expect(() =>
      execFileSync(venvPython, [seed, "--credits-per-tier", "200", "--free-questions", "3"], {
        cwd: repoRoot,
        env: poisonedEnv(),
        encoding: "utf-8",
      }),
    ).not.toThrow();
  });

  test("resolveDatabaseUrl under the live suite's own environment targets PG_PORT", () => {
    // Whatever this run inherited, the URL the specs seed through points at
    // the stack that is actually up. Only meaningful when the run declares a
    // PG_PORT — without one there is no second opinion to prefer.
    test.skip(!process.env.PG_PORT, "run declares no PG_PORT");
    expect(resolveDatabaseUrl()).toContain(`:${livePort}/`);
  });
});
