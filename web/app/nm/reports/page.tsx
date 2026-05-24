"use client";

import { useState, useEffect } from "react";
import { useNmState, useReports } from "../lib/hooks";
import { Panel } from "../components/panel";

export default function ReportsPage() {
  const { state } = useNmState();
  const { reports } = useReports();
  const [activeId, setActiveId] = useState<string | null>(null);
  const [reportContent, setReportContent] = useState<string | null>(null);

  const allReports = [
    ...(state
      ? [
          {
            id: "current",
            date: `Current run · ${state.run_id}`,
            current: true,
          },
        ]
      : []),
    ...reports.map((r: { id: string; date: string }) => ({
      id: r.id,
      date: new Date(r.date).toLocaleDateString("en-US", {
        year: "numeric",
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      }),
      current: false,
    })),
  ];

  useEffect(() => {
    if (allReports.length > 0 && !activeId) {
      setActiveId(allReports[0].id);
    }
  }, [allReports.length, activeId]);

  useEffect(() => {
    if (!activeId || activeId === "current") {
      setReportContent(null);
      return;
    }
    fetch(`/api/nm/reports?id=${activeId}`)
      .then((r) => r.text())
      .then(setReportContent)
      .catch(() => setReportContent(null));
  }, [activeId]);

  const issues = state ? Object.values(state.issues) : [];
  const merged = issues.filter((i) => i.status === "merged").length;
  const failed = issues.filter((i) => i.status === "failed").length;
  const inFlight = issues.filter(
    (i) => i.status === "in_progress" || i.status === "reviewing",
  ).length;
  const queued = issues.filter((i) => i.status === "queued").length;

  return (
    <div className="nm-report">
      <Panel
        id="ARCH"
        title="REPORT ARCHIVE"
        right={<span>{allReports.length}</span>}
        flush
      >
        <div className="nm-report-list">
          {allReports.map((rep) => (
            <div
              key={rep.id}
              className={`nm-report-list__item ${activeId === rep.id ? "nm-report-list__item--active" : ""}`}
              onClick={() => setActiveId(rep.id)}
            >
              <div className="nm-report-list__date">{rep.date}</div>
              {rep.current && (
                <div className="nm-report-list__sum">
                  <span className="nm-ok">
                    <b>{merged}</b> merged
                  </span>
                  <span className="nm-err">
                    <b>{failed}</b> failed
                  </span>
                  <span className="nm-mute nm-right">ongoing</span>
                </div>
              )}
            </div>
          ))}
          {allReports.length === 0 && (
            <div
              style={{
                padding: 20,
                color: "var(--text-mute)",
                textAlign: "center",
              }}
            >
              no reports yet
            </div>
          )}
        </div>
      </Panel>

      <Panel
        id="RPT"
        title={`REPORT · ${activeId || "—"}`}
        right={
          activeId && activeId !== "current" ? (
            <span>.night-manager/{activeId}.md</span>
          ) : null
        }
        flush
      >
        <div className="nm-report-doc">
          {activeId === "current" && state ? (
            <>
              <h1>Night Manager Run &middot; In Progress</h1>
              <p className="nm-lede">
                Run <span className="nm-prim">{state.run_id}</span> &middot;
                started {new Date(state.started_at).toLocaleTimeString()} &middot;{" "}
                <span className="nm-info">
                  {inFlight} agents active
                </span>{" "}
                &middot; final report will be generated on completion.
              </p>
              <div className="nm-stats-strip">
                <div className="nm-stats-strip__cell">
                  <div className="nm-stats-strip__lbl">Merged</div>
                  <div className="nm-stats-strip__val nm-ok">{merged}</div>
                </div>
                <div className="nm-stats-strip__cell">
                  <div className="nm-stats-strip__lbl">Failed</div>
                  <div className="nm-stats-strip__val nm-err">{failed}</div>
                </div>
                <div className="nm-stats-strip__cell">
                  <div className="nm-stats-strip__lbl">In flight</div>
                  <div className="nm-stats-strip__val">{inFlight}</div>
                </div>
                <div className="nm-stats-strip__cell">
                  <div className="nm-stats-strip__lbl">Queued</div>
                  <div className="nm-stats-strip__val">{queued}</div>
                </div>
              </div>
            </>
          ) : reportContent ? (
            <div
              style={{ whiteSpace: "pre-wrap", fontFamily: "var(--font-nm)" }}
            >
              {reportContent}
            </div>
          ) : (
            <div
              style={{
                padding: 40,
                color: "var(--text-mute)",
                textAlign: "center",
              }}
            >
              {activeId
                ? "loading report…"
                : "select a report from the archive"}
            </div>
          )}
        </div>
      </Panel>
    </div>
  );
}
