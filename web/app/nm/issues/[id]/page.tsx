"use client";

import { use } from "react";
import Link from "next/link";
import { useNmState } from "../../lib/hooks";
import { IssueDetail } from "../../components/issue-detail";

export default function IssueDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const { state, isLoading } = useNmState();

  if (isLoading) {
    return (
      <div
        style={{
          display: "grid",
          placeItems: "center",
          height: "100%",
          color: "var(--text-mute)",
          fontSize: 13,
          letterSpacing: "0.12em",
          textTransform: "uppercase",
        }}
      >
        loading&hellip;
      </div>
    );
  }

  if (!state) {
    return (
      <div style={{ padding: 40, color: "var(--text-mute)", textAlign: "center" }}>
        <div style={{ marginBottom: 16 }}>No active run</div>
        <Link href="/nm" className="nm-btn nm-btn--ghost" style={{ textDecoration: "none" }}>
          &larr; Dashboard
        </Link>
      </div>
    );
  }

  const issue = state.issues[id];
  if (!issue) {
    return (
      <div style={{ padding: 40, color: "var(--text-mute)", textAlign: "center" }}>
        <div style={{ marginBottom: 16 }}>
          Issue {id} not found in current run
        </div>
        <Link href="/nm" className="nm-btn nm-btn--ghost" style={{ textDecoration: "none" }}>
          &larr; Dashboard
        </Link>
      </div>
    );
  }

  return <IssueDetail issue={issue} />;
}
