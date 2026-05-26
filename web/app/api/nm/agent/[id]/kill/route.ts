import { readFile } from "fs/promises";
import { STATE_PATH } from "../../../paths";

export async function POST(
  _request: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;

  try {
    const raw = await readFile(STATE_PATH(), "utf-8");
    const state = JSON.parse(raw);
    const issue = state.issues?.[id];

    if (!issue?.pid) {
      return Response.json(
        { ok: false, error: `No PID found for ${id}` },
        { status: 404 },
      );
    }

    const pid = Number(issue.pid);
    if (!Number.isInteger(pid) || pid <= 0) {
      return Response.json(
        { ok: false, error: `Invalid PID for ${id}` },
        { status: 400 },
      );
    }

    try {
      process.kill(pid, "SIGTERM");
      return Response.json({ ok: true, pid });
    } catch (killError) {
      return Response.json(
        { ok: false, error: String(killError) },
        { status: 500 },
      );
    }
  } catch (e) {
    return Response.json(
      { ok: false, error: String(e) },
      { status: 500 },
    );
  }
}
