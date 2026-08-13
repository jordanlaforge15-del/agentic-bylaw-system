// ABS-498 — the dev DB backup keeps a snapshot that outlives the week.
//
// Before this issue, `scripts/backup-dev-db.sh` rotated seven day-of-week
// slots in place: day 8 overwrote day 1, so the oldest recoverable state was
// always 7 days old and a defect noticed on day 8 was unrecoverable. The fix
// adds two promotion tiers (weekly x4, monthly x6) on top of the daily seven.
//
// There is no browser surface here — the deliverable is a shell script — so
// this spec drives the script itself, the same way `tdd-docs.spec.ts` drives
// repo artifacts through node:fs. What it exercises is the real script, not a
// re-implementation of it: each run shells out to `scripts/backup-dev-db.sh`
// with a fake `docker` shim on PATH (streaming a date-tagged payload in place
// of pg_dump output) and the script's `BYLAW_BACKUP_DATE` clock hook advanced
// one simulated day per run. That makes a multi-month retention horizon
// assertable in seconds without Docker, Postgres, or waiting.
//
// Assertions, in the order the acceptance criteria state them:
//   1. A day-1 snapshot is still on disk and byte-for-byte restorable on
//      day 30 — the defect this issue exists to close. Note which tier holds
//      it there: the weekly tier's horizon is only ~28 days, so past that it
//      is the monthly promotion doing the work.
//   2. Disk stays bounded: never more than 7 + 4 + 6 = 17 dump files, with
//      the documented per-tier counts, across a 240-day horizon (long enough
//      to saturate the slowest tier and force it to prune).
//   3. The prune is prefix-scoped — a hand-copied keepsake that matches no
//      tier pattern survives a 120-day horizon untouched.
//   4. `--dry-run` reports the plan and changes nothing (not one file, not
//      one byte of backup.log).

import { spawnSync } from "node:child_process";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

import { expect, test } from "../fixtures/test-env";

const REPO_ROOT = path.resolve(__dirname, "..", "..", "..");
const SCRIPT = path.join(REPO_ROOT, "scripts", "backup-dev-db.sh");

// Must match the defaults documented in docs/DEV_DB_BACKUP.md.
const KEEP_DAILY = 7;
const KEEP_WEEKLY = 4;
const KEEP_MONTHLY = 6;
const MAX_SLOTS = KEEP_DAILY + KEEP_WEEKLY + KEEP_MONTHLY; // 17

/**
 * Write a `docker` shim into binDir that the script finds via PATH. It answers
 * `docker inspect` with a running container and streams a payload tagged with
 * the injected date, so any dump can be traced back to the day it was captured.
 */
function writeFakeDocker(binDir: string): void {
  const shim = `#!/usr/bin/env bash
case "$1" in
  inspect) echo "true"; exit 0 ;;
  exec)    printf 'PGDMP-%s\\n' "\${BYLAW_BACKUP_DATE:-unknown}"; exit 0 ;;
  *)       echo "fake docker: unsupported verb $1" >&2; exit 2 ;;
esac
`;
  const p = path.join(binDir, "docker");
  fs.writeFileSync(p, shim);
  fs.chmodSync(p, 0o755);
}

/** A scratch backup dir + a PATH whose `docker` is the shim above. */
function makeSandbox(): { backupDir: string; env: NodeJS.ProcessEnv } {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "abs498-"));
  const backupDir = path.join(root, "backups");
  const binDir = path.join(root, "bin");
  fs.mkdirSync(backupDir);
  fs.mkdirSync(binDir);
  writeFakeDocker(binDir);
  return {
    backupDir,
    env: {
      ...process.env,
      PATH: `${binDir}:${process.env.PATH ?? ""}`,
      BYLAW_BACKUP_DIR: backupDir,
      BYLAW_KEEP_WEEKLY: String(KEEP_WEEKLY),
      BYLAW_KEEP_MONTHLY: String(KEEP_MONTHLY),
    },
  };
}

/**
 * Run the backup script once, pretending "today" is `isoDate`. Returns the
 * script's stderr, where every `log()` line lands. Throws on a nonzero exit,
 * so a broken run fails the test at the call site rather than silently
 * leaving the assertions to interpret an empty backup dir.
 */
function runBackup(
  env: NodeJS.ProcessEnv,
  isoDate: string,
  args: string[] = [],
): string {
  const res = spawnSync(SCRIPT, args, {
    env: { ...env, BYLAW_BACKUP_DATE: isoDate },
    encoding: "utf8",
  });
  if (res.status !== 0) {
    throw new Error(
      `backup-dev-db.sh ${args.join(" ")} exited ${res.status} on ${isoDate}:\n${res.stderr}`,
    );
  }
  return res.stderr;
}

/** `start` plus `offset` days, as YYYY-MM-DD. */
function addDays(start: string, offset: number): string {
  const d = new Date(`${start}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + offset);
  return d.toISOString().slice(0, 10);
}

const dumps = (dir: string): string[] =>
  fs.readdirSync(dir).filter((f) => f.endsWith(".dump")).sort();

test.describe("dev DB backup retention (ABS-498)", () => {
  test("a day-1 snapshot is still restorable on day 30", async () => {
    test.setTimeout(120_000);
    const { backupDir, env } = makeSandbox();
    // Start on a Monday so day 1 opens both a fresh ISO week and (being the
    // 1st) a fresh calendar month — the two tiers that must outlive day 8.
    const start = "2026-06-01";

    for (let day = 0; day < 30; day++) {
      runBackup(env, addDays(start, day));
    }

    const dayOnePayload = `PGDMP-${start}\n`;
    const survivors = dumps(backupDir).filter(
      (f) => fs.readFileSync(path.join(backupDir, f), "utf8") === dayOnePayload,
    );

    // The pre-fix behaviour: day 8 overwrote layer1-Mon.dump, so by day 30
    // nothing carrying day 1's bytes remained anywhere on disk.
    expect(survivors.length).toBeGreaterThan(0);
    // The monthly tier is what carries day 1 this far. The weekly slot that
    // captured it (W23) is *correctly* gone by now — 30 days spans five ISO
    // weeks and the tier keeps four, so the weekly horizon is ~28 days. That
    // is the division of labour between the tiers, not a gap.
    expect(survivors).toContain("layer1-monthly-2026-06.dump");
    expect(fs.existsSync(path.join(backupDir, "layer1-weekly-2026-W23.dump"))).toBe(
      false,
    );

    // Still, the weekly tier alone already beats the old 7-day floor: its
    // oldest surviving slot is three weeks back.
    const weekly = dumps(backupDir).filter((f) => f.startsWith("layer1-weekly-"));
    expect(weekly).toHaveLength(KEEP_WEEKLY);
    expect(weekly[0]).toBe("layer1-weekly-2026-W24.dump");

    // Byte-for-byte, not merely present — a truncated promotion is not a
    // restorable snapshot.
    expect(
      fs.readFileSync(path.join(backupDir, "layer1-monthly-2026-06.dump")),
    ).toEqual(Buffer.from(dayOnePayload));

    // And the daily slot for that same weekday has indeed moved on, which is
    // exactly why the promotion tiers are needed.
    expect(fs.readFileSync(path.join(backupDir, "layer1-Mon.dump"), "utf8")).not
      .toBe(dayOnePayload);
  });

  // 240 days = eight calendar months, which is what it takes to saturate the
  // six-slot monthly tier and force it to prune. A shorter horizon would leave
  // the tier under its limit and never exercise the prune at all.
  test("disk stays bounded at 17 slots across a 240-day horizon", async () => {
    test.setTimeout(300_000);
    const { backupDir, env } = makeSandbox();
    const start = "2026-01-01";

    for (let day = 0; day < 240; day++) {
      runBackup(env, addDays(start, day));
      expect(dumps(backupDir).length).toBeLessThanOrEqual(MAX_SLOTS);
    }

    const files = dumps(backupDir);
    expect(files.filter((f) => /^layer1-[A-Z][a-z]{2}\.dump$/.test(f))).toHaveLength(
      KEEP_DAILY,
    );
    expect(files.filter((f) => f.startsWith("layer1-weekly-"))).toHaveLength(
      KEEP_WEEKLY,
    );
    expect(files.filter((f) => f.startsWith("layer1-monthly-"))).toHaveLength(
      KEEP_MONTHLY,
    );
    expect(files).toHaveLength(MAX_SLOTS);

    // The prune keeps the NEWEST slots — retention that dropped the recent
    // end would satisfy the count assertion above while being useless.
    const monthly = files.filter((f) => f.startsWith("layer1-monthly-"));
    expect(monthly[monthly.length - 1]).toBe("layer1-monthly-2026-08.dump");
    expect(monthly[0]).toBe("layer1-monthly-2026-03.dump");
  });

  test("prune is prefix-scoped — a hand-copied keepsake survives", async () => {
    test.setTimeout(180_000);
    const { backupDir, env } = makeSandbox();
    const start = "2026-01-01";
    // The real one of these (layer1-pre-data-model-3.0-20260812.dump) is the
    // reason we noticed the gap; it matches no tier glob and must be inert.
    const keepsake = path.join(backupDir, "layer1-pre-data-model-3.0-20260101.dump");
    fs.writeFileSync(keepsake, "PGDMP-keepsake\n");

    for (let day = 0; day < 120; day++) {
      runBackup(env, addDays(start, day));
    }

    expect(fs.existsSync(keepsake)).toBe(true);
    expect(fs.readFileSync(keepsake, "utf8")).toBe("PGDMP-keepsake\n");
  });

  test("--dry-run reports the plan and changes nothing", async () => {
    const { backupDir, env } = makeSandbox();
    const start = "2026-03-02";
    for (let day = 0; day < 10; day++) {
      runBackup(env, addDays(start, day));
    }

    const snapshot = (): Record<string, string> =>
      Object.fromEntries(
        fs
          .readdirSync(backupDir)
          .sort()
          .map((f) => [f, fs.readFileSync(path.join(backupDir, f), "base64")]),
      );

    const before = snapshot();
    const stderr = runBackup(env, addDays(start, 10), ["--dry-run"]);

    // It reports a plan...
    expect(stderr).toContain("would write layer1-Thu.dump");
    expect(stderr).toContain("no files were changed");
    // ...and every line is marked as a simulation, so a dry run can never be
    // mistaken for a real one when read back out of a terminal scrollback.
    for (const line of stderr.trim().split("\n")) {
      expect(line).toContain("DRY-RUN");
    }

    // backup.log included: a dry run that appends to the log has changed state.
    expect(snapshot()).toEqual(before);
  });
});
