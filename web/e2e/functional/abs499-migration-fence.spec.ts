// Functional: ABS-499 — pre-migration snapshot fence + drift check
//
// The fence refuses to let anything mutate the *dev* database without a
// labelled, rotation-exempt snapshot landing first. It has no web-UI surface,
// so these tests drive the real scripts with spawnSync (the pattern from
// abs286-compare-ab-runs.spec.ts) against the live e2e stack — the one place
// a real migrated Postgres exists during the suite.
//
// What the stack proves that unit tests cannot:
//
//  1. The e2e stack booted *with the fence in the tree*. scripts/e2e-up.sh runs
//     `alembic upgrade head`; if the fence's scope gate were wrong, that would
//     have aborted and no spec in this suite would run at all. /healthz
//     reporting database=ok is the receipt.
//  2. The live e2e DSN is out of the fence's scope. This is the regression that
//     would wedge every worktree, so it is asserted against the URL the stack
//     is actually on, not a fabricated one.
//  3. The drift check agrees a real, freshly-migrated database is at head —
//     the complement to the unit tests, which drive synthetic revisions.
//  4. A fenced entry point aborts cleanly (exit 3, no traceback) when the
//     snapshot fails, without having touched the database.

import { spawnSync } from "child_process";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { E2E_API_URL, expect, test } from "../fixtures/test-env";
import { resolveDatabaseUrl } from "../helpers/database-url";

const REPO_ROOT = path.resolve(__dirname, "../../../");
const VENV_PYTHON = path.join(REPO_ROOT, ".venv", "bin", "python");
const DRIFT_CHECK = path.join(REPO_ROOT, "scripts", "check_migration_drift.py");
const BACKFILL_PARCELS = path.join(REPO_ROOT, "scripts", "backfill_parcels.py");

const databaseUrl = resolveDatabaseUrl();

function runPython(
  args: string[],
  env: NodeJS.ProcessEnv = {},
): { stdout: string; stderr: string; status: number } {
  const result = spawnSync(VENV_PYTHON, args, {
    cwd: REPO_ROOT,
    encoding: "utf-8",
    env: { ...process.env, ...env },
  });
  return {
    stdout: result.stdout ?? "",
    stderr: result.stderr ?? "",
    status: result.status ?? -1,
  };
}

/** A snapshot script that always fails, standing in for a dead container. */
function failingSnapshotScript(): { script: string; calls: string } {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "abs499-"));
  const calls = path.join(dir, "calls.txt");
  const script = path.join(dir, "failing-snapshot.sh");
  fs.writeFileSync(
    script,
    [
      "#!/usr/bin/env bash",
      `printf '%s\\n' "$1" >> ${calls}`,
      "echo \"ERROR: container 'agentic-bylaw-system-postgres-1' is not running\" >&2",
      "exit 1",
      "",
    ].join("\n"),
  );
  fs.chmodSync(script, 0o755);
  return { script, calls };
}

test.describe("Pre-migration snapshot fence (ABS-499)", () => {
  test("(1) the stack migrated to completion with the fence in the tree", async () => {
    const res = await fetch(`${E2E_API_URL}/healthz`);
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.checks.database).toBe("ok");
  });

  test("(2) the live e2e database is out of the fence's scope", () => {
    const probe = runPython([
      "-c",
      "import sys; from layer1.db.migration_fence import targets_dev_database;" +
        " print(targets_dev_database(sys.argv[1]))",
      databaseUrl,
    ]);

    expect(probe.status, probe.stderr).toBe(0);
    // False, or the e2e stack would demand a dev-DB snapshot on every boot.
    expect(probe.stdout.trim()).toBe("False");
  });

  test("(3) the drift check reports the migrated e2e database as in sync", () => {
    const result = runPython([DRIFT_CHECK, "--database-url", databaseUrl]);

    expect(result.stdout).toContain("in sync — no pending migrations");
    expect(result.status, result.stdout + result.stderr).toBe(0);
  });

  test("(4) a failed snapshot aborts the entry point before it writes", () => {
    const { script, calls } = failingSnapshotScript();

    const result = runPython([BACKFILL_PARCELS, "--database-url", databaseUrl], {
      // Force the fence on: the e2e DB is deliberately out of scope, and the
      // point here is the refusal path, not the gate (covered by test 2).
      BYLAW_FORCE_MIGRATION_SNAPSHOT: "1",
      BYLAW_SKIP_MIGRATION_SNAPSHOT: "",
      GITHUB_ACTIONS: "",
      BYLAW_SNAPSHOT_SCRIPT: script,
    });

    expect(result.status).toBe(3);
    expect(result.stderr).toContain("ABORT:");
    expect(result.stderr).toContain("refusing to mutate");
    expect(result.stderr).not.toContain("Traceback");
    // The fence ran, with this script's tag, and nothing followed it.
    expect(fs.readFileSync(calls, "utf-8").trim()).toBe("backfill-parcels");
    expect(result.stdout).not.toContain("backfill_parcels:");
  });

  test("(5) the stack is still healthy after the refused migration", async () => {
    const res = await fetch(`${E2E_API_URL}/healthz`);
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.checks.database).toBe("ok");
  });
});
