import { execFile } from "child_process";

export async function POST() {
  return new Promise<Response>((resolve) => {
    execFile("tmux", ["kill-session", "-t", "night-manager"], (error) => {
      if (error) {
        resolve(
          Response.json({ ok: false, error: error.message }, { status: 500 }),
        );
      } else {
        resolve(Response.json({ ok: true }));
      }
    });
  });
}
