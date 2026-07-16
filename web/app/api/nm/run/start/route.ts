import { execFile } from "child_process";
import { PROJECT_ROOT } from "../../paths";
import {
  startAccessLogEntry,
  writeAccessLog,
  type AccessLogEntry,
} from "../../access-log";

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

// Helper: write the audit entry and return a JSON Response that also
// carries the correlation id, both as a header and inside the body.
// The correlation id lets an operator copy it from the browser devtools
// network panel and grep .night-manager/api-access.log for the matching
// entry.
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
  // Build the access-log entry as early as possible — even if the body
  // is malformed we want a record that *something* hit the endpoint.
  const entry = startAccessLogEntry(request, "/api/nm/run/start");

  let body: Record<string, unknown>;
  try {
    body = await request.json();
  } catch {
    entry.outcome = "rejected";
    entry.note = "invalid json body";
    return finishAndRespond(
      entry,
      { ok: false, error: "Invalid JSON body" },
      { status: 400 },
    );
  }

  // Whitelisted body fingerprint — agentModel + dryRun only. Never log
  // labels, issue ids, or resume payloads (they may include caller text).
  entry.bodyFingerprint = {
    agentModel:
      typeof body.agentModel === "string" ? body.agentModel : undefined,
    dryRun: typeof body.dryRun === "boolean" ? body.dryRun : undefined,
  };

  const errors = validateBody(body);
  if (errors.length > 0) {
    entry.outcome = "rejected";
    entry.note = `validation: ${errors.map((e) => e.field).join(",")}`;
    return finishAndRespond(
      entry,
      { ok: false, errors },
      { status: 400 },
    );
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
    entry.testMode = true;
    entry.outcome = "test_mode";
    entry.launcherExec = false;
    return finishAndRespond(entry, { ok: true, testMode: true, argv });
  }

  return new Promise<Response>((resolve) => {
    execFile(
      "./scripts/start-night-manager.sh",
      argv,
      { cwd: PROJECT_ROOT(), env: process.env },
      (error) => {
        if (error) {
          // ABS-154's guard refuses-to-clobber lands here too — the
          // distinction between "guard refused" and "launcher crashed"
          // lives in error.message, which we summarize but never log
          // verbatim (it can include shell output).
          entry.outcome = "refused";
          entry.launcherExec = false;
          // Truncate aggressively — the message is for the dashboard,
          // not for forensic debugging. The full stderr is still in
          // night_manager.log if needed.
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
          entry.launcherExec = true;
          resolve(finishAndRespond(entry, { ok: true }));
        }
      },
    );
  });
}
