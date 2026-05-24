import { readFile } from "fs/promises";
import { join } from "path";
import { exec } from "child_process";

const STATE_PATH = join(process.cwd(), "..", ".night-manager", "state.json");

export async function POST(
  _request: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;

  try {
    const raw = await readFile(STATE_PATH, "utf-8");
    const state = JSON.parse(raw);
    const issue = state.issues?.[id];

    if (!issue?.pid) {
      return Response.json(
        { ok: false, error: `No PID found for ${id}` },
        { status: 404 },
      );
    }

    return new Promise<Response>((resolve) => {
      exec(`kill -TERM ${issue.pid}`, (error) => {
        if (error) {
          resolve(
            Response.json(
              { ok: false, error: error.message },
              { status: 500 },
            ),
          );
        } else {
          resolve(Response.json({ ok: true, pid: issue.pid }));
        }
      });
    });
  } catch (e) {
    return Response.json(
      { ok: false, error: String(e) },
      { status: 500 },
    );
  }
}
