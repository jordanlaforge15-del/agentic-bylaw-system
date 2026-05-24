"use client";

import type { Status } from "../lib/types";
import { STATUS_LABEL, STATUS_TONE } from "../lib/status";

export function StatusPill({ status }: { status: Status }) {
  const tone = STATUS_TONE[status] || "queued";
  return (
    <span className={`nm-pill nm-pill--${tone}`}>
      {status === "in_progress" ? (
        <span className="nm-ring" />
      ) : (
        <span className={`nm-dot nm-dot--${tone}`} />
      )}
      {STATUS_LABEL[status] || status?.toUpperCase()}
    </span>
  );
}
