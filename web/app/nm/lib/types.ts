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
  branch: string | null;
  worktree: string | null;
  ports: { pg: number; api: number; web: number } | null;
  session_id: string | null;
  pid: number | null;
  log_file: string | null;
  attempts: number;
  review_attempts: number;
  started_at: string | null;
  completed_at: string | null;
  merged_at: string | null;
  error: string | null;
  currentTool?: string | null;
  currentTarget?: string | null;
  summary?: string | null;
};

export type Group = {
  group: number;
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

export type LogEvent = {
  t: string;
  kind: "tool" | "assistant" | "review";
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
