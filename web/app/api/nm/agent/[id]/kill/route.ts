// POST /api/nm/agent/[id]/kill — kill a single agent worker by PID.
//
// Instrumented per ABS-155: the route logs which agent was targeted in
// the access-log note (no caller-controlled text — only the dashboard-
// supplied issue identifier, which already matches ABS-\d+).

import { readFile } from "fs/promises";
import { exec } from "child_process";
import { STATE_PATH } from "../../../paths";
import {
  startAccessLogEntry,
  writeAccessLog,
  type AccessLogEntry,
} from "../../../access-log";

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

export async function POST(
  request: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  const entry = startAccessLogEntry(request, `/api/nm/agent/${id}/kill`);

  try {
    const raw = await readFile(STATE_PATH(), "utf-8");
    const state = JSON.parse(raw);
    const issue = state.issues?.[id];

    if (!issue?.pid) {
      entry.outcome = "error";
      entry.note = `no pid for ${id}`;
      return finishAndRespond(
        entry,
        { ok: false, error: `No PID found for ${id}` },
        { status: 404 },
      );
    }

    return new Promise<Response>((resolve) => {
      exec(`kill -TERM ${issue.pid}`, (error) => {
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
          resolve(finishAndRespond(entry, { ok: true, pid: issue.pid }));
        }
      });
    });
  } catch (e) {
    entry.outcome = "error";
    entry.note = "state.json read failed";
    return finishAndRespond(
      entry,
      { ok: false, error: String(e) },
      { status: 500 },
    );
  }
}
