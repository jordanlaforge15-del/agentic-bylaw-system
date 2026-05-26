// ABS-155: "API access — last 10" dashboard panel.
//
// Reads from /api/nm/access-log?limit=10 every 10s and renders the
// most-recent hits color-coded by outcome:
//
//   * ok        → green   (operator-initiated launch / kill landed)
//   * test_mode → amber   (NM_TEST_MODE=1 bypass — Playwright/dev hit)
//   * rejected  → grey    (400 from validation, not a real launch)
//   * refused   → red     (500 from ABS-154 guard or launcher crash)
//   * error     → red     (unhandled exception / kill-target missing)
//
// The panel is silently empty for non-admins (the endpoint 401s and we
// surface no entries) — leaking the panel's existence to a signed-in
// operator without admin rights is fine; the dashboard is already
// behind /app's auth gate.
"use client";

import { useAccessLog } from "../lib/hooks";
import type { AccessLogEntry, AccessOutcome } from "../lib/types";
import { Panel } from "./panel";
import { Dot } from "./dot";

// Map outcome → CSS tone classname suffix. Re-uses the existing syslog
// tone palette so the colors line up with the orchestrator-log panel
// above it. Grey (mute) is the default for boring rejected validation.
const OUTCOME_TONE: Record<AccessOutcome, string> = {
  ok: "ok",
  test_mode: "warn",
  rejected: "info",
  refused: "err",
  error: "err",
};

const OUTCOME_LABEL: Record<AccessOutcome, string> = {
  ok: "OK",
  test_mode: "TEST",
  rejected: "400",
  refused: "REF",
  error: "ERR",
};

function fmtTime(iso: string): string {
  // The log writes ISO timestamps; the dashboard shows HH:MM:SS only —
  // anything older than today is visible from the date column in the
  // raw JSON file if the operator wants to dig.
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toTimeString().slice(0, 8);
  } catch {
    return iso;
  }
}

function routeShortName(route: string): string {
  // Drop the /api/nm/ prefix — every entry has it. Keeps the panel
  // dense at 11px.
  return route.replace(/^\/api\/nm\//, "");
}

export function AccessLogPanel() {
  const entries = useAccessLog();

  return (
    <Panel
      id="API-04"
      title="API ACCESS · LAST 10"
      flush
      right={<span>poll 10s</span>}
    >
      <div className="nm-syslog" data-testid="nm-access-log-panel">
        {entries.length === 0 ? (
          <div
            className="nm-syslog__ln nm-syslog__ln--info"
            data-testid="nm-access-log-empty"
          >
            <span className="nm-syslog__t">--:--:--</span>
            <span className="nm-syslog__lvl">idle</span>
            <span className="nm-syslog__msg">no hits yet</span>
          </div>
        ) : (
          entries.map((e) => <AccessLogRow key={e.correlationId} entry={e} />)
        )}
      </div>
    </Panel>
  );
}

function AccessLogRow({ entry }: { entry: AccessLogEntry }) {
  const tone = OUTCOME_TONE[entry.outcome];
  const label = OUTCOME_LABEL[entry.outcome];
  // Sec-Fetch-Site:null is the strongest signal of a non-browser source
  // — a curl or server-side fetch never sets it. Surface it as a small
  // "src:?" hint so the operator can spot stray POSTs immediately.
  const browserHint = entry.secFetchSite ? entry.secFetchSite : "no-browser";
  return (
    <div
      className={`nm-syslog__ln nm-syslog__ln--${tone}`}
      data-testid="nm-access-log-row"
      data-outcome={entry.outcome}
      title={`correlationId: ${entry.correlationId}\nUA: ${entry.userAgent ?? "—"}\nOrigin: ${entry.origin ?? "—"}\nReferer: ${entry.referer ?? "—"}\nSec-Fetch-Site: ${entry.secFetchSite ?? "—"}\nRemote: ${entry.remoteAddr ?? "—"}${entry.note ? `\nNote: ${entry.note}` : ""}`}
    >
      <span className="nm-syslog__t">{fmtTime(entry.ts)}</span>
      <span className="nm-syslog__lvl">
        <Dot tone={tone} />
        {" " + label}
      </span>
      <span className="nm-syslog__msg">
        <span className="nm-prim">{entry.method}</span>{" "}
        {routeShortName(entry.route)}{" "}
        <span className="nm-mute">· src:{browserHint}</span>
        {entry.bodyFingerprint.agentModel && (
          <span className="nm-mute">
            {" · "}m:{entry.bodyFingerprint.agentModel}
          </span>
        )}
        {entry.bodyFingerprint.dryRun ? (
          <span className="nm-mute"> · dry</span>
        ) : null}
      </span>
    </div>
  );
}
