"use client";

import Link from "next/link";
import { useNmState } from "./lib/hooks";
import { Dashboard } from "./components/dashboard";

export default function NmDashboardPage() {
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
        loading state.json&hellip;
      </div>
    );
  }

  if (!state) {
    return (
      <div
        style={{
          display: "grid",
          placeItems: "center",
          height: "100%",
          color: "var(--text-mute)",
        }}
      >
        <div style={{ textAlign: "center" }}>
          <div
            style={{
              fontSize: 14,
              letterSpacing: "0.12em",
              textTransform: "uppercase",
              marginBottom: 16,
            }}
          >
            NO ACTIVE RUN
          </div>
          <div style={{ fontSize: 12, marginBottom: 24 }}>
            No state.json found. Launch a new Night Manager run to get started.
          </div>
          <Link
            href="/nm/launch"
            className="nm-btn nm-btn--prim"
            style={{ textDecoration: "none" }}
          >
            &#9654; NEW RUN
          </Link>
        </div>
      </div>
    );
  }

  return <Dashboard state={state} />;
}
