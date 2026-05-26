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

  // ABS-153: test-mode bypass. When NM_TEST_MODE=1 (set by scripts/e2e-up.sh for
  // the worktree's Next.js dev), short-circuit before exec so Playwright specs
  // that exercise this endpoint never actually launch NM. The returned `cmd`
  // lets tests assert on argument formatting / quoting without side effects.
  if (process.env.NM_TEST_MODE === "1") {
    const parts: string[] = ["./scripts/start-night-manager.sh"];
    for (let i = 0; i < argv.length; i++) {
      if (argv[i] === "--label" && i + 1 < argv.length) {
        parts.push(argv[i], `"${argv[++i]}"`);
      } else {
        parts.push(argv[i]);
      }
    }
    const cmd = parts.join(" ");
    return Response.json({ ok: true, testMode: true, cmd });
  }

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
