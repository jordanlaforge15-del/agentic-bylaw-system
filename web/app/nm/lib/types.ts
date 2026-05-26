export type Status =
  | "queued"
  | "in_progress"
  | "reviewing"
  | "merged"
  | "failed"
  | "blocked";

export type Issue = {
  identifier: string;
  title: string;
  status: Status;
  branch: string;
  worktree: string;
  ports: { pg: number; api: number; web: number } | null;
  session_id: string;
  pid: number;
  log_file: string;
  attempts: number;
  review_attempts: number;
  started_at: string | null;
  completed_at: string | null;
  merged_at: string | null;
  error: string | null;
  linear_id: string;
};

export type Group = {
  parallel: string[];
  deploy: boolean;
};

export type RunConfig = {
  max_agents: number;
  label: string;
  model: string;
  deploy: boolean;
};

export type RunState = {
  run_id: string;
  started_at: string;
  config: RunConfig;
  plan: Group[];
  issues: Record<string, Issue>;
};

export type RawStreamEvent = {
  type: "assistant" | "user" | "system" | "result";
  subtype?: string;
  message?: {
    content?: Array<{
      type: string;
      name?: string;
      input?: Record<string, unknown>;
      text?: string;
      thinking?: string;
    }>;
  };
  session_id?: string;
};

export type ParsedLogEvent = {
  ts: string;
  kind: "tool" | "text" | "thinking" | "system";
  name?: string;
  args?: string;
  text?: string;
};

export type SystemLogEntry = {
  t: string;
  level: "info" | "ok" | "warn" | "err";
  msg: string;
};

export type ReportSummary = {
  id: string;
  date: string;
  merged: number;
  failed: number;
  blocked: number;
  duration: string;
  current?: boolean;
};

export type NmTheme = "apollo" | "vanguard" | "red_october";

// ABS-155: mirror of AccessLogEntry from
// web/app/api/nm/access-log.ts — UI-side shape for the dashboard
// panel. Kept in sync by hand; the access-log file is the source of
// truth. If a new field is added there, mirror it here (or trim it
// out if the panel doesn't need it).
export type AccessOutcome =
  | "ok"
  | "test_mode"
  | "rejected"
  | "refused"
  | "error";

export type AccessLogEntry = {
  ts: string;
  correlationId: string;
  route: string;
  method: string;
  remoteAddr: string | null;
  userAgent: string | null;
  referer: string | null;
  origin: string | null;
  secFetchSite: string | null;
  secFetchMode: string | null;
  secFetchDest: string | null;
  testMode: boolean;
  launcherExec: boolean;
  bodyFingerprint: { agentModel?: string; dryRun?: boolean };
  status: number;
  outcome: AccessOutcome;
  note?: string;
};
