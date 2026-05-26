import { exec } from "child_process";
import { PROJECT_ROOT } from "../../paths";

export async function POST(request: Request) {
  const body = await request.json();
  const {
    maxAgents = 3,
    label = "Triaged",
    model = "opus",
    agentModel = "opus",
    agentEffort = "high",
    agentTokenLimit = 10,
    reviewerModel = "sonnet",
    reviewerTokenLimit = 2,
    deploy = false,
    issue,
    dryRun = false,
    resume = false,
    resumeIssue,
    resumeQueued = false,
  } = body;

  const args = [
    `--max-agents ${maxAgents}`,
    `--label "${label}"`,
    `--model ${model}`,
    `--agent-model ${agentModel}`,
    `--agent-effort ${agentEffort}`,
    `--agent-token-limit ${agentTokenLimit}`,
    `--reviewer-model ${reviewerModel}`,
    `--reviewer-token-limit ${reviewerTokenLimit}`,
  ];
  if (deploy) args.push("--deploy");
  if (issue) args.push(`--issue ${issue}`);
  if (dryRun) args.push("--dry-run");
  if (resume) args.push("--resume");
  if (resumeIssue) args.push(`--resume-issue ${resumeIssue}`);
  if (resumeQueued) args.push("--resume-queued");

  const cmd = `./scripts/start-night-manager.sh ${args.join(" ")}`;

  return new Promise<Response>((resolve) => {
    exec(cmd, { cwd: PROJECT_ROOT(), env: process.env }, (error) => {
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
