// ABS-155: HTTP audit-trail sink for /api/nm/run/* endpoints.
//
// Background: on 2026-05-26 at 10:11:03 the main checkout's
// /api/nm/run/start was POST'd to by an unidentified process, killing
// a live Night Manager run. ABS-152/153/154 made the launcher resilient
// (refuses to clobber a live session), but didn't *identify* the trigger.
//
// This module is the diagnostic step before deciding the real fix: write
// every NM control-plane HTTP hit to a newline-delimited JSON file so the
// next stray POST is fully attributed.
//
// Design choices:
//   * sync `fs.appendFileSync` rather than async writes — these endpoints
//     are low-frequency (an operator clicks once or twice per session,
//     not per second) and we want the side-effect to happen before
//     `execFile` spawns the child, so a panic mid-execute can't lose the
//     audit record.
//   * NDJSON, not a DB table — `grep`-able, no migration coupling, and
//     the rotation story is just "rename the file". The existing
//     night_manager.log already follows this pattern.
//   * No PII / secrets. Authorization headers, cookies, and the full
//     request body are explicitly excluded. We capture the body's
//     `agentModel` + `dryRun` flag only — enough to fingerprint the
//     test fixture vs. an operator launch, not enough to leak label
//     text or resume-issue payloads.
//   * `correlationId` is a uuid stamped on every entry and returned in
//     the response. A successful or failed POST can be cross-referenced
//     with this id from the response body in browser devtools (or curl
//     output) back to the log entry.

import { mkdirSync, appendFileSync, readFileSync, existsSync } from "fs";
import { join, dirname } from "path";
import { randomUUID } from "crypto";
import { NM_DIR } from "./paths";

export const ACCESS_LOG_PATH = () => join(NM_DIR(), "api-access.log");

// Outcomes are coarse-grained on purpose: the dashboard color-codes
// each entry by this field, so adding a new value requires a UI change.
// Keep this list small and stable.
export type AccessOutcome =
  | "ok"          // 200 — launcher exec'd successfully or kill landed
  | "test_mode"   // 200 — NM_TEST_MODE=1 short-circuit (ABS-153 bypass)
  | "rejected"    // 400 — validation failed (ABS-143 input gate)
  | "refused"    // 500 — launcher refused (ABS-154 guard) or other 500
  | "error";      // 500 — unhandled exception or kill-target missing

export interface AccessLogEntry {
  ts: string;                // ISO timestamp
  correlationId: string;     // uuid, also returned in response body
  route: string;             // e.g. "/api/nm/run/start"
  method: string;            // "POST" / "GET"
  remoteAddr: string | null; // X-Forwarded-For (first hop) or null
  userAgent: string | null;
  referer: string | null;
  origin: string | null;
  // Browser-fired fetches set these; curl / server-side fetches don't.
  // A null value here on a "successful" POST is a strong signal that
  // the request did NOT come from a real browser tab.
  secFetchSite: string | null;
  secFetchMode: string | null;
  secFetchDest: string | null;
  // Whether the ABS-153 NM_TEST_MODE bypass was hit. True in Playwright,
  // false in real operator launches.
  testMode: boolean;
  // The launcher's behavior. Together with `outcome` this answers
  // "did the request actually spawn night_manager?".
  launcherExec: boolean;
  // Whitelisted body fingerprint. Never includes labels, resume-issues,
  // or any field that could carry caller-controlled text.
  bodyFingerprint: {
    agentModel?: string;
    dryRun?: boolean;
  };
  // HTTP status returned to the caller.
  status: number;
  outcome: AccessOutcome;
  // Hand-wave error description for refused / error outcomes. Never
  // contains the request body or stack frames — just a one-line summary
  // suitable for the dashboard.
  note?: string;
}

// Build an AccessLogEntry from a Request, stamping a fresh correlation
// id. The caller fills in `status`, `outcome`, `testMode`, etc. once it
// knows them, then passes the populated entry to `writeAccessLog`.
export function startAccessLogEntry(
  request: Request,
  route: string,
  bodyFingerprint: AccessLogEntry["bodyFingerprint"] = {},
): AccessLogEntry {
  const h = request.headers;
  // X-Forwarded-For may be a comma-separated list; the first entry is
  // the originating client. If the proxy chain is absent (direct dev
  // hit) the header will be null — that's fine.
  const xff = h.get("x-forwarded-for");
  const remoteAddr = xff ? xff.split(",")[0]!.trim() : null;
  return {
    ts: new Date().toISOString(),
    correlationId: randomUUID(),
    route,
    method: request.method,
    remoteAddr,
    userAgent: h.get("user-agent"),
    referer: h.get("referer"),
    origin: h.get("origin"),
    secFetchSite: h.get("sec-fetch-site"),
    secFetchMode: h.get("sec-fetch-mode"),
    secFetchDest: h.get("sec-fetch-dest"),
    testMode: false,
    launcherExec: false,
    bodyFingerprint,
    status: 0,
    outcome: "ok",
  };
}

// Append one entry to .night-manager/api-access.log. Sync write so the
// audit record is durable before the route handler returns / spawns a
// child process. Failures are swallowed with a console.error — we'd
// rather lose a log line than break /api/nm/run/start because the disk
// is full.
export function writeAccessLog(entry: AccessLogEntry): void {
  try {
    const path = ACCESS_LOG_PATH();
    mkdirSync(dirname(path), { recursive: true });
    appendFileSync(path, JSON.stringify(entry) + "\n", "utf-8");
  } catch (e) {
    // Never let logging take down the endpoint.
    console.error("nm-access-log: failed to append", e);
  }
}

// Read the last `limit` entries from the log. Used by GET /api/nm/access-log.
// We slurp the file (it's bounded by operator clicks; even a year of
// daily launches is ~365 KB) and tail-slice; if it ever grows past a
// few MB we can swap in a reverse-line reader.
export function readAccessLog(limit: number): AccessLogEntry[] {
  const path = ACCESS_LOG_PATH();
  if (!existsSync(path)) return [];
  let raw: string;
  try {
    raw = readFileSync(path, "utf-8");
  } catch {
    return [];
  }
  const lines = raw.split("\n").filter(Boolean);
  const slice = lines.slice(-Math.max(1, Math.min(limit, 1000)));
  const entries: AccessLogEntry[] = [];
  for (const line of slice) {
    try {
      entries.push(JSON.parse(line) as AccessLogEntry);
    } catch {
      // Corrupt line — skip, don't poison the dashboard.
    }
  }
  // Newest first for UI consumption.
  return entries.reverse();
}
