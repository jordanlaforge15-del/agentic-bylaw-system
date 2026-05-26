import { execFile } from "child_process";
import { PROJECT_ROOT } from "../../paths";

const ALLOWED_MODELS = ["opus", "sonnet", "haiku"] as const;
const ALLOWED_EFFORTS = ["low", "medium", "high"] as const;
const ISSUE_RE = /^[A-Z]+-\d+$/;
const LABEL_RE = /^[A-Za-z0-9 _-]{1,64}$/;

function validInt(v: unknown, min: number, max: number): number | null {
  const n = Number(v);
  if (!Number.isFinite(n) || n < min || n > max) return null;
  return Math.round(n);
}

function validFloat(v: unknown, min: number, max: number): number | null {
  const n = Number(v);
  if (!Number.isFinite(n) || n < min || n > max) return null;
  return n;
}

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

  const errors: string[] = [];

  const safeMaxAgents = validInt(maxAgents, 1, 10);
  if (safeMaxAgents === null) errors.push("maxAgents must be 1–10");

  if (!LABEL_RE.test(label)) errors.push("label must be 1–64 alphanumeric/space/dash/underscore chars");

  if (!ALLOWED_MODELS.includes(model)) errors.push(`model must be one of: ${ALLOWED_MODELS.join(", ")}`);
  if (!ALLOWED_MODELS.includes(agentModel)) errors.push(`agentModel must be one of: ${ALLOWED_MODELS.join(", ")}`);
  if (!ALLOWED_EFFORTS.includes(agentEffort)) errors.push(`agentEffort must be one of: ${ALLOWED_EFFORTS.join(", ")}`);
  if (!ALLOWED_MODELS.includes(reviewerModel)) errors.push(`reviewerModel must be one of: ${ALLOWED_MODELS.join(", ")}`);

  const safeAgentTokenLimit = validFloat(agentTokenLimit, 0.5, 50);
  if (safeAgentTokenLimit === null) errors.push("agentTokenLimit must be 0.5–50");

  const safeReviewerTokenLimit = validFloat(reviewerTokenLimit, 0.5, 20);
  if (safeReviewerTokenLimit === null) errors.push("reviewerTokenLimit must be 0.5–20");

  if (issue && !ISSUE_RE.test(issue)) errors.push("issue must match PROJECT-123 format");
  if (resumeIssue && !ISSUE_RE.test(resumeIssue)) errors.push("resumeIssue must match PROJECT-123 format");

  if (errors.length > 0) {
    return Response.json({ ok: false, errors }, { status: 400 });
  }

  const argv: string[] = [
    "--max-agents", String(safeMaxAgents),
    "--label", label,
    "--model", model,
    "--agent-model", agentModel,
    "--agent-effort", agentEffort,
    "--agent-token-limit", String(safeAgentTokenLimit),
    "--reviewer-model", reviewerModel,
    "--reviewer-token-limit", String(safeReviewerTokenLimit),
  ];
  if (deploy) argv.push("--deploy");
  if (issue) argv.push("--issue", issue);
  if (dryRun) argv.push("--dry-run");
  if (resume) argv.push("--resume");
  if (resumeIssue) argv.push("--resume-issue", resumeIssue);
  if (resumeQueued) argv.push("--resume-queued");

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
    execFile(
      "./scripts/start-night-manager.sh",
      argv,
      { cwd: PROJECT_ROOT(), env: process.env },
      (error) => {
        if (error) {
          resolve(
            Response.json({ ok: false, error: error.message }, { status: 500 }),
          );
        } else {
          resolve(Response.json({ ok: true }));
        }
      },
    );
  });
}
