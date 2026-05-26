// POST /api/nm/run/kill — tear down the active Night Manager tmux session.
//
// Instrumented per ABS-155 so we can attribute any kill request the same
// way we attribute launches. The actual tmux call is unchanged — only
// the audit-log side-effect was added.

import { exec } from "child_process";
import {
  startAccessLogEntry,
  writeAccessLog,
  type AccessLogEntry,
} from "../../access-log";

function finishAndRespond(
  entry: AccessLogEntry,
  body: Record<string, unknown>,
  init: ResponseInit = {},
): Response {
  entry.status = init.status ?? 200;
  writeAccessLog(entry);
  const responseBody = { ...body, correlationId: entry.correlationId };
  const headers = new Headers(init.headers);
  headers.set("X-NM-Correlation-Id", entry.correlationId);
  return Response.json(responseBody, { ...init, headers });
}

export async function POST(request: Request) {
  const entry = startAccessLogEntry(request, "/api/nm/run/kill");

  return new Promise<Response>((resolve) => {
    exec("tmux kill-session -t night-manager", (error) => {
      if (error) {
        entry.outcome = "error";
        entry.note = String(error.message).split("\n")[0]!.slice(0, 160);
        resolve(
          finishAndRespond(
            entry,
            { ok: false, error: error.message },
            { status: 500 },
          ),
        );
      } else {
        entry.outcome = "ok";
        resolve(finishAndRespond(entry, { ok: true }));
      }
    });
  });
}
