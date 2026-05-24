import type { Status } from "./types";

export const STATUS_LABEL: Record<Status, string> = {
  queued: "QUEUED",
  in_progress: "RUNNING",
  reviewing: "REVIEW",
  merged: "MERGED",
  failed: "FAILED",
  blocked: "BLOCKED",
};

export const STATUS_TONE: Record<Status, string> = {
  queued: "queued",
  in_progress: "info",
  reviewing: "review",
  merged: "ok",
  failed: "err",
  blocked: "warn",
};

export type GroupState =
  | "deploy"
  | "complete"
  | "active"
  | "queued"
  | "mixed";

export const GROUP_TONE: Record<GroupState, string> = {
  deploy: "queued",
  complete: "ok",
  active: "info",
  queued: "queued",
  mixed: "warn",
};
