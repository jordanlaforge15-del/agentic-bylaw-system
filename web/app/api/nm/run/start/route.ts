import { execFile } from "child_process";
import { PROJECT_ROOT } from "../../paths";

const MODEL_RE = /^[a-z][a-z0-9._-]{0,63}$/;
const ISSUE_RE = /^ABS-\d{1,6}$/;
const LABEL_RE = /^[A-Za-z0-9 _-]{1,64}$/;
const ALLOWED_EFFORTS = ["low", "medium", "high"];

type ValidationError = { field: string; reason: string };

function validateBody(body: Record<string, unknown>): ValidationError[] {
  const errors: ValidationError[] = [];

  const {
    maxAgents,
    label,
    model,
    agentModel,
    agentEffort,
    agentTokenLimit,
    reviewerModel,
    reviewerTokenLimit,
    issue,
    resumeIssue,
  } = body;

  if (maxAgents !== undefined) {
    const n = Number(maxAgents);
    if (!Number.isInteger(n) || n < 1 || n > 20)
      errors.push({ field: "maxAgents", reason: "integer 1–20" });
  }

  if (label !== undefined && !LABEL_RE.test(String(label)))
    errors.push({ field: "label", reason: "alphanumeric/space/dash/underscore, 1–64 chars" });

  if (model !== undefined && !MODEL_RE.test(String(model)))
    errors.push({ field: "model", reason: "lowercase alphanumeric/dot/dash/underscore" });

  if (agentModel !== undefined && !MODEL_RE.test(String(agentModel)))
    errors.push({ field: "agentModel", reason: "lowercase alphanumeric/dot/dash/underscore" });

  if (agentEffort !== undefined && !ALLOWED_EFFORTS.includes(String(agentEffort)))
    errors.push({ field: "agentEffort", reason: "one of: low, medium, high" });

  if (agentTokenLimit !== undefined) {
    const n = Number(agentTokenLimit);
    if (!Number.isFinite(n) || n < 1 || n > 1000)
      errors.push({ field: "agentTokenLimit", reason: "number 1–1000" });
  }

  if (reviewerModel !== undefined && !MODEL_RE.test(String(reviewerModel)))
    errors.push({ field: "reviewerModel", reason: "lowercase alphanumeric/dot/dash/underscore" });

  if (reviewerTokenLimit !== undefined) {
    const n = Number(reviewerTokenLimit);
    if (!Number.isFinite(n) || n < 1 || n > 1000)
      errors.push({ field: "reviewerTokenLimit", reason: "number 1–1000" });
  }

  if (issue !== undefined && issue !== null && !ISSUE_RE.test(String(issue)))
    errors.push({ field: "issue", reason: "must match ABS-<number>" });

  if (resumeIssue !== undefined && resumeIssue !== null && !ISSUE_RE.test(String(resumeIssue)))
    errors.push({ field: "resumeIssue", reason: "must match ABS-<number>" });

  return errors;
}

export async function POST(request: Request) {
  const body = await request.json();

  const errors = validateBody(body);
  if (errors.length > 0) {
    return Response.json({ ok: false, errors }, { status: 400 });
  }

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

  const argv: string[] = [
    "--max-agents", String(Number(maxAgents)),
    "--label", String(label),
    "--model", String(model),
    "--agent-model", String(agentModel),
    "--agent-effort", String(agentEffort),
    "--agent-token-limit", String(Number(agentTokenLimit)),
    "--reviewer-model", String(reviewerModel),
    "--reviewer-token-limit", String(Number(reviewerTokenLimit)),
  ];
  if (deploy) argv.push("--deploy");
  if (issue) argv.push("--issue", String(issue));
  if (dryRun) argv.push("--dry-run");
  if (resume) argv.push("--resume");
  if (resumeIssue) argv.push("--resume-issue", String(resumeIssue));
  if (resumeQueued) argv.push("--resume-queued");

  if (process.env.NM_TEST_MODE === "1") {
    return Response.json({ ok: true, testMode: true, argv });
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
