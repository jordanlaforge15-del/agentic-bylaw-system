// ISSUE DETAIL — drill-down view with live log stream, timeline, actions.

function IssueDetail({ identifier, onBack, fakeNow }) {
  const issue = NM_DATA.issues[identifier];
  if (!issue) {
    return (
      <Panel id="ERR-404" title="ISSUE NOT FOUND">
        <p>No issue with id {identifier}.</p>
        <button className="btn" onClick={onBack}>← Back</button>
      </Panel>
    );
  }

  const logs = NM_LOGS[identifier] || [];
  const streamed = useStreamingLog(logs, 2000, logs.length);

  const startedMs = parseISO(issue.started_at);
  const completedMs = parseISO(issue.completed_at);
  const mergedMs = parseISO(issue.merged_at);
  const isLive = issue.status === "in_progress" || issue.status === "reviewing";
  const elapsedMs = isLive ? fakeNow - startedMs : (completedMs ? completedMs - startedMs : null);

  const tone = STATUS_TONE[issue.status];

  return (
    <div className="col" style={{ gap: 12 }}>
      {/* Header strip */}
      <div className="panel" style={{ padding: 0 }}>
        <span className="tick-bl" />
        <span className="tick-br" />
        <div style={{ display: "grid", gridTemplateColumns: "auto 1fr auto", alignItems: "stretch" }}>
          <div style={{ padding: "16px 20px", borderRight: "1px solid var(--line)", display: "flex", flexDirection: "column", gap: 4, minWidth: 160 }}>
            <div className="up" style={{ color: "var(--text-mute)" }}>Issue</div>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <Dot tone={tone} pulse={isLive} />
              <span style={{ fontSize: 22, color: "var(--primary)", fontWeight: 600, letterSpacing: "0.04em" }}>{issue.identifier}</span>
            </div>
            <StatusPill status={issue.status} />
          </div>
          <div style={{ padding: "16px 20px", display: "flex", flexDirection: "column", gap: 6, minWidth: 0 }}>
            <div className="up" style={{ color: "var(--text-mute)" }}>Title</div>
            <div style={{ fontSize: 16, color: "var(--text)" }}>{issue.title}</div>
            <div className="row" style={{ gap: 18, marginTop: 2, fontSize: 11, color: "var(--text-mute)" }}>
              <span><b className="dim">branch ›</b> {issue.branch || "—"}</span>
              <span><b className="dim">worktree ›</b> {issue.worktree || "—"}</span>
            </div>
          </div>
          <div style={{ padding: "16px 20px", borderLeft: "1px solid var(--line)", display: "flex", gap: 18, alignItems: "center" }}>
            <button className="btn btn--ghost" onClick={onBack}>← Back</button>
            {isLive && (
              <>
                <button className="btn">▶ RETRY</button>
                <button className="btn">↷ SKIP</button>
                <button className="btn btn--danger">■ KILL</button>
              </>
            )}
            {issue.status === "failed" && <button className="btn btn--prim">▶ RETRY</button>}
            {issue.status === "merged" && <button className="btn">View Diff</button>}
          </div>
        </div>
      </div>

      <div className="issue">
        <div className="issue__main">
          {issue.error && (
            <Panel id="ERR" title="ERROR" right={<span>last attempt</span>}>
              <div className="errbox">
                <span className="icn">!</span>
                <div>
                  <div style={{ marginBottom: 6, color: "var(--err)", fontWeight: 600, letterSpacing: "0.06em", textTransform: "uppercase" }}>
                    E2E TEST FAILURE
                  </div>
                  <div>{issue.error}</div>
                  <div style={{ marginTop: 12, fontSize: 11, color: "var(--text-mute)" }}>
                    attempts {issue.attempts} / 2 · review attempts {issue.review_attempts}
                  </div>
                </div>
              </div>
            </Panel>
          )}

          <Panel
            id="LOG"
            title="AGENT STREAM"
            right={
              <span>
                {isLive && <span><span className="dot dot--info dot--pulse" /> LIVE · </span>}
                {logs.length} events · {issue.log_file || `.night-manager/logs/${issue.identifier}.jsonl`}
              </span>
            }
            flush
          >
            <div className="fulllog">
              {streamed.length === 0 && (
                <div style={{ padding: 24, color: "var(--text-mute)", textAlign: "center" }}>
                  agent not yet spawned · waiting in queue
                </div>
              )}
              {streamed.map((ev, i) => (
                <div key={i} className={`ev ev--${ev.kind}`}>
                  <span className="t">+{ev.t}</span>
                  {ev.kind === "tool" && (
                    <span><span className="name">{ev.name}</span><span className="args">{ev.args}</span></span>
                  )}
                  {ev.kind === "assistant" && (
                    <span className="txt">{ev.text}</span>
                  )}
                  {ev.kind === "review" && (
                    <span className="txt">{ev.text}</span>
                  )}
                </div>
              ))}
              {isLive && (
                <div className="ev">
                  <span className="t">live</span>
                  <span className="prim cursor"></span>
                </div>
              )}
            </div>
          </Panel>

          <Panel id="DIFF" title="WORKING DIFF" right={<span>HEAD → agent/{issue.identifier}</span>} flush>
            <DiffPreview issue={issue} />
          </Panel>
        </div>

        <div className="issue__side">
          <Panel id="META" title="METADATA">
            <div className="kv-grid">
              <span className="k">Identifier</span><span className="v prim">{issue.identifier}</span>
              <span className="k">Status</span><span className="v"><StatusPill status={issue.status} /></span>
              <span className="k">Attempts</span><span className="v">{issue.attempts}</span>
              <span className="k">Reviews</span><span className="v">{issue.review_attempts}</span>
              <span className="k">Branch</span><span className="v truncate">{issue.branch || "—"}</span>
              <span className="k">Worktree</span><span className="v truncate" style={{ fontSize: 11 }}>{issue.worktree || "—"}</span>
              <span className="k">PG Port</span><span className="v">{issue.ports?.pg ?? "—"}</span>
              <span className="k">API Port</span><span className="v">{issue.ports?.api ?? "—"}</span>
              <span className="k">Web Port</span><span className="v">{issue.ports?.web ?? "—"}</span>
              <span className="k">PID</span><span className="v">{isLive ? `~${42100 + (issue.ports?.api % 100 || 0)}` : "—"}</span>
              {elapsedMs != null && (<>
                <span className="k">{isLive ? "Elapsed" : "Duration"}</span>
                <span className="v prim">{fmtElapsed(elapsedMs)}</span>
              </>)}
            </div>
          </Panel>

          <Panel id="TIM" title="TIMELINE" flush>
            <div className="timeline">
              <TimelineEvent t="23:00:00" txt="Issue planned" done />
              {startedMs && <TimelineEvent t={fmtClock(startedMs)} txt="Agent spawned" done tone="info" />}
              {streamed.length > 4 && <TimelineEvent t={fmtClock(startedMs + 1000 * 60 * 8)} txt="First diff written" done />}
              {completedMs ? (
                <TimelineEvent t={fmtClock(completedMs)} txt="Coding complete" done />
              ) : isLive ? (
                <TimelineEvent t={fmtClock(fakeNow)} txt="Coding in progress" tone="info" pulse />
              ) : (
                <TimelineEvent t="—" txt="Coding complete" pending />
              )}
              {issue.review_attempts > 0 && (
                <TimelineEvent
                  t={completedMs ? fmtClock(completedMs + 30000) : "—"}
                  txt={`Review pass ${issue.review_attempts}`}
                  done={issue.status !== "reviewing"}
                  tone={issue.status === "reviewing" ? "review" : (issue.status === "merged" ? "ok" : "err")}
                  pulse={issue.status === "reviewing"}
                />
              )}
              {mergedMs ? (
                <TimelineEvent t={fmtClock(mergedMs)} txt="Merged to dev" done tone="ok" />
              ) : issue.status === "failed" ? (
                <TimelineEvent t={fmtClock(completedMs)} txt="Marked failed" done tone="err" />
              ) : (
                <TimelineEvent t="—" txt="Merge to dev" pending />
              )}
            </div>
          </Panel>

          <Panel id="ACT" title="ACTIONS" flush>
            <div style={{ padding: 12, display: "flex", flexDirection: "column", gap: 8 }}>
              <button className="btn"><span>▶</span> Re-spawn agent</button>
              <button className="btn"><span>↷</span> Mark as blocked</button>
              <button className="btn"><span>▶</span> Open worktree in editor</button>
              <button className="btn"><span>▶</span> Tail JSONL in terminal</button>
              <button className="btn btn--danger"><span>■</span> SIGTERM agent process</button>
            </div>
          </Panel>
        </div>
      </div>
    </div>
  );
}

function TimelineEvent({ t, txt, done, pending, tone, pulse }) {
  const dotColor = tone ? `var(--${tone})` : (done ? "var(--text-dim)" : "var(--text-mute)");
  return (
    <div className="timeline__ev">
      <span
        className="timeline__dot"
        style={{
          background: pending ? "transparent" : dotColor,
          border: pending ? "1px dashed var(--text-mute)" : "none",
          animation: pulse ? "pulse 1.4s ease-in-out infinite" : "none",
          boxShadow: !pending && tone ? `0 0 8px ${dotColor}` : "none",
        }}
      />
      <span className="timeline__t">{t}</span>
      <span className="timeline__txt" style={{ color: pending ? "var(--text-mute)" : "var(--text)" }}>{txt}</span>
    </div>
  );
}

// Synthetic diff preview — looks credible without being a real diff parser
function DiffPreview({ issue }) {
  const lines = issue.status === "in_progress" || issue.status === "reviewing" ? [
    { kind: "f", text: `web/components/widgets/SalesCard.tsx` },
    { kind: "h", text: "@@ -42,11 +42,18 @@ export function SalesCard() {" },
    { kind: "c", text: "  const { data, isLoading } = useWidgetData('sales');" },
    { kind: "r", text: "  if (isLoading) return <Skeleton />;" },
    { kind: "a", text: "  // Defer the skeleton 120ms to avoid flicker on warm data" },
    { kind: "a", text: "  const showSkeleton = useDeferredLoading(isLoading, 120);" },
    { kind: "a", text: "  if (showSkeleton) return <MemoizedSkeleton />;" },
    { kind: "c", text: "" },
    { kind: "c", text: "  return (" },
    { kind: "c", text: "    <Card>" },
    { kind: "f", text: `web/components/widgets/RevenueCard.tsx` },
    { kind: "h", text: "@@ -22,8 +22,11 @@ export function RevenueCard() {" },
    { kind: "r", text: "  const skeleton = <RevenueSkeleton />;" },
    { kind: "a", text: "  const skeleton = useMemo(() => <RevenueSkeleton />, []);" },
    { kind: "c", text: "" },
  ] : issue.status === "failed" ? [
    { kind: "f", text: `web/components/invoices/ExportButton.tsx` },
    { kind: "h", text: "@@ -1,0 +1,42 @@" },
    { kind: "a", text: "+ // 42 lines added — see worktree" },
    { kind: "f", text: `api/invoices/export.py` },
    { kind: "h", text: "@@ -1,0 +1,68 @@" },
    { kind: "a", text: "+ // 68 lines added — streaming CSV response" },
  ] : [
    { kind: "f", text: `web/auth/loginRedirect.ts` },
    { kind: "h", text: "@@ -18,6 +18,9 @@" },
    { kind: "r", text: "  document.cookie = `nm_redirect=${path}; path=/`;" },
    { kind: "a", text: "  sessionStorage.setItem('nm_redirect', path);" },
    { kind: "a", text: "  // Safari iOS drops document.cookie writes in this context" },
  ];

  return (
    <div style={{ padding: "10px 0", fontFamily: "var(--font-mono)", fontSize: 12, lineHeight: 1.55, maxHeight: 280, overflowY: "auto" }}>
      {lines.map((ln, i) => (
        <div key={i} style={{ padding: "1px 16px", display: "flex", gap: 12,
          background: ln.kind === "a" ? "rgba(111, 220, 140, 0.06)" : ln.kind === "r" ? "rgba(255, 91, 59, 0.07)" : "transparent",
          color: ln.kind === "f" ? "var(--primary)" : ln.kind === "h" ? "var(--info)" : ln.kind === "a" ? "var(--ok)" : ln.kind === "r" ? "var(--err)" : "var(--text-dim)",
          fontWeight: ln.kind === "f" ? 600 : 400,
        }}>
          <span style={{ width: 14, color: "var(--text-mute)", flexShrink: 0 }}>
            {ln.kind === "a" && "+"}
            {ln.kind === "r" && "−"}
            {ln.kind === "f" && "›"}
            {ln.kind === "h" && "@"}
          </span>
          <span>{ln.text}</span>
        </div>
      ))}
    </div>
  );
}

Object.assign(window, { IssueDetail });
