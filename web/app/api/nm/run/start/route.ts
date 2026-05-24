import { exec } from "child_process";
import { join } from "path";

const PROJECT_ROOT = join(process.cwd(), "..");

export async function POST(request: Request) {
  const body = await request.json();
  const {
    maxAgents = 3,
    label = "Triaged",
    model = "opus",
    deploy = false,
    issue,
    dryRun = false,
  } = body;

  const args = [
    `--max-agents ${maxAgents}`,
    `--label "${label}"`,
    `--model ${model}`,
  ];
  if (deploy) args.push("--deploy");
  if (issue) args.push(`--issue ${issue}`);
  if (dryRun) args.push("--dry-run");

  const cmd = `./scripts/start-night-manager.sh ${args.join(" ")}`;

  return new Promise<Response>((resolve) => {
    exec(cmd, { cwd: PROJECT_ROOT, env: process.env }, (error) => {
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
