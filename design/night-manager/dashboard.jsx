// DASHBOARD — primary mission console view.
// Top: KPI strip. Then a 2-col grid: plan timeline + agent board on left, system log on right.

function Dashboard({ onOpenIssue, fakeNow }) {
  const { plan, issues } = NM_DATA;
  const all = Object.values(issues);
  const counts = useMemo(() => {
    const c = { queued: 0, in_progress: 0, reviewing: 0, merged: 0, failed: 0, blocked: 0 };
    all.forEach((i) => { c[i.status] = (c[i.status] || 0) + 1; });
    return c;
  }, [all]);

  const elapsedMs = fakeNow - RUN_EPOCH;
  const total = all.length;
  const processed = counts.merged + counts.failed + counts.blocked;
  const remaining = total - processed;
  // crude ETA: avg time per processed * remaining
  const avgMs = processed > 0 ? elapsedMs / processed : 0;
  const etaMs = avgMs * remaining;

  return (
    <div className="col" style={{ gap: 12 }}>
      <KpiStrip
        elapsedMs={elapsedMs}
        counts={counts}
        total={total}
        etaMs={etaMs}
      />

      <div className="dash">
        <PlanTimeline plan={plan} issues={issues} onOpen={onOpenIssue} />
        <AgentBoard issues={issues} onOpen={onOpenIssue} fakeNow={fakeNow} />
        <SidePanels fakeNow={fakeNow} counts={counts} elapsedMs={elapsedMs} />
      </div>
    </div>
  );
}

// ─────────── KPI STRIP
function KpiStrip({ elapsedMs, counts, total, etaMs }) {
  const processed = counts.merged + counts.failed + counts.blocked;
  return (
    <div className="kpis">
      <KPI label="Elapsed" value={fmtElapsed(elapsedMs)} tone="prim" />
      <KPI label="Agents Active" value={counts.in_progress + counts.reviewing} unit={`/ ${NM_DATA.config.max_agents}`} tone="prim" />
      <KPI label="Merged" value={counts.merged} unit={`/ ${total}`} tone="ok" />
      <KPI label="Failed" value={counts.failed} tone={counts.failed > 0 ? "err" : ""} />
      <KPI label="Queued" value={counts.queued} />
      <KPI label="ETA" value={fmtElapsed(etaMs)} hint={`avg ${fmtMinutes(processed > 0 ? elapsedMs / processed : 0)} / issue`} />
    </div>
  );
}

// ─────────── PLAN TIMELINE
function PlanTimeline({ plan, issues, onOpen }) {
  const summary = `${plan.length} groups · ${Object.keys(issues).length} issues`;
  return (
    <Panel
      id="PLN-01"
      title="EXECUTION PLAN"
      right={<span>{summary}</span>}
      flush
      className="dash__plan"
    >
      {plan.map((group) => (
        <PlanRow key={group.group} group={group} issues={issues} onOpen={onOpen} />
      ))}
    </Panel>
  );
}

function PlanRow({ group, issues, onOpen }) {
  const slots = group.parallel.map((id) => issues[id]).filter(Boolean);
  const merged = slots.filter((i) => i.status === "merged").length;
  const running = slots.filter((i) => i.status === "in_progress" || i.status === "reviewing").length;
  const failed = slots.filter((i) => i.status === "failed").length;
  const queued = slots.filter((i) => i.status === "queued").length;

  // pad to 4 columns visually so groups align
  const padded = [...slots];
  while (padded.length < 4 && !group.deploy) padded.push(null);

  // group state
  const groupState =
    group.deploy ? "deploy" :
    slots.every((s) => s.status === "merged") ? "complete" :
    slots.some((s) => s.status === "in_progress" || s.status === "reviewing") ? "active" :
    slots.every((s) => s.status === "queued") ? "queued" :
    "mixed";

  const groupTone = {
    deploy: "queued",
    complete: "ok",
    active: "info",
    queued: "queued",
    mixed: "warn",
  }[groupState];

  return (
    <div className={`plan-row ${group.deploy ? "plan-row--deploy" : ""}`}>
      <div className="plan-row__label">
        <span className="tag">GROUP</span>
        <span className="name">G{String(group.group).padStart(2, "0")}</span>
        <span className="up" style={{ marginTop: 4, color: "var(--text-mute)" }}>
          <Dot tone={groupTone} pulse={groupState === "active"} /> {groupState}
        </span>
      </div>

      <div className="plan-row__slots">
        {group.deploy ? (
          <div className="deploy-card">
            <span style={{ color: "var(--primary)" }}>▲</span>
            DEPLOY · PROMOTE DEV → STAGING → PROD
            <span className="mute">awaiting groups 1–3</span>
          </div>
        ) : (
          padded.map((it, i) => it ? (
            <PlanSlot key={it.identifier} issue={it} onOpen={onOpen} />
          ) : (
            <div key={`empty-${i}`} className="plan-slot plan-slot--empty">·</div>
          ))
        )}
      </div>

      <div className="plan-row__sum">
        {!group.deploy ? (
          <>
            <div className="display" style={{ fontSize: 15 }}>{merged}/{slots.length}</div>
            <div className="up" style={{ textAlign: "right", color: "var(--text-mute)" }}>
              {running > 0 && <span><span className="info">●</span> {running} run · </span>}
              {failed > 0 && <span><span className="err">●</span> {failed} fail · </span>}
              {queued > 0 && <span><span className="queued">●</span> {queued} q · </span>}
              {merged > 0 && <span><span className="ok">●</span> {merged} ok</span>}
            </div>
          </>
        ) : (
          <span className="up" style={{ color: "var(--text-mute)" }}>queued</span>
        )}
      </div>
    </div>
  );
}

function PlanSlot({ issue, onOpen }) {
  const tone = STATUS_TONE[issue.status];
  return (
    <div
      className={`plan-slot plan-slot--${issue.status === "in_progress" ? "running" : issue.status}`}
      onClick={() => onOpen(issue.identifier)}
    >
      <div className="plan-slot__head">
        <Dot tone={tone} pulse={issue.status === "in_progress"} />
        <span className="plan-slot__id">{issue.identifier}</span>
        <span className="up right" style={{ color: `var(--${tone})` }}>
          {STATUS_LABEL[issue.status]}
        </span>
      </div>
      <div className="plan-slot__title">{issue.title}</div>
      <div className="plan-slot__foot">
        <span>attempt {issue.attempts || 0}</span>
        {issue.ports && <span className="right">:{issue.ports.web}</span>}
      </div>
    </div>
  );
}

// ─────────── AGENT BOARD (live cards for in_progress + reviewing)
function AgentBoard({ issues, onOpen, fakeNow }) {
  const active = Object.values(issues).filter(
    (i) => i.status === "in_progress" || i.status === "reviewing"
  );
  return (
    <Panel
      id="OPS-02"
      title="ACTIVE AGENTS"
      right={
        <span>
          <span className="dot dot--info dot--pulse" /> LIVE · POLL 2s
        </span>
      }
      flush
      className="dash__agents"
    >
      <div className="agents-grid" style={{ padding: 12 }}>
        {active.map((it) => (
          <AgentCard key={it.identifier} issue={it} onOpen={onOpen} fakeNow={fakeNow} />
        ))}
        {/* one queued slot to show what queued looks like */}
        <QueuedSlot issue={issues["ABS-98"]} />
        <QueuedSlot issue={issues["ABS-99"]} />
      </div>
    </Panel>
  );
}

function AgentCard({ issue, onOpen, fakeNow }) {
  const startedMs = parseISO(issue.started_at);
  const elapsedMs = fakeNow - startedMs;
  const logs = NM_LOGS[issue.identifier] || [];
  // simulate streaming — show all events but visually paced
  const streamed = useStreamingLog(logs, 2400, Math.max(2, logs.length - 1));

  const isReviewing = issue.status === "reviewing";
  const tone = STATUS_TONE[issue.status];

  return (
    <div className="agent-card" onClick={() => onOpen(issue.identifier)} style={{ cursor: "pointer" }}>
      <div className="agent-card__head">
        <Dot tone={tone} pulse />
        <span className="agent-card__id">{issue.identifier}</span>
        <span className="agent-card__title truncate">{issue.title}</span>
        <StatusPill status={issue.status} />
      </div>
      <div className="agent-card__body">
        <div className="agent-card__meta">
          <span><b>elapsed</b> <span className="prim">{fmtElapsed(elapsedMs)}</span></span>
          <span><b>attempt</b> {issue.attempts}{issue.review_attempts > 0 && ` · rev ${issue.review_attempts}`}</span>
          <span><b>branch</b> <span className="truncate" style={{ display: "inline-block", maxWidth: 130 }}>{issue.branch?.replace("agent/", "")}</span></span>
          <span><b>ports</b> {issue.ports?.web}/{issue.ports?.api}</span>
        </div>

        {issue.currentTool && (
          <div className="agent-card__current">
            <div className="lbl">▶ Current tool call</div>
            <div className="val">
              <span className="tool">{issue.currentTool}</span>
              <span className="mute"> · </span>
              <span className="dim" style={{ wordBreak: "break-all" }}>{issue.currentTarget}</span>
            </div>
          </div>
        )}

        <div className="minilog">
          {streamed.slice(-5).map((ev, i) => (
            <div className="line" key={i}>
              <span className="t">{ev.t}</span>
              {ev.kind === "tool" && (
                <span><span className="name">{ev.name}</span> <span className="mute">{ev.args}</span></span>
              )}
              {ev.kind === "assistant" && (
                <span><span className="name assistant">▌</span> <span className="dim truncate" style={{ display: "inline-block", maxWidth: 220 }}>{ev.text}</span></span>
              )}
              {ev.kind === "review" && (
                <span><span style={{ color: "var(--review)" }}>review</span> <span className="dim">{ev.text}</span></span>
              )}
            </div>
          ))}
          <div className="line">
            <span className="t">·</span>
            <span className="prim cursor"></span>
          </div>
        </div>
      </div>
    </div>
  );
}

function QueuedSlot({ issue }) {
  return (
    <div className="agent-card" style={{ opacity: 0.5, cursor: "default" }}>
      <div className="agent-card__head">
        <Dot tone="queued" />
        <span className="agent-card__id" style={{ color: "var(--text-mute)" }}>{issue.identifier}</span>
        <span className="agent-card__title truncate">{issue.title}</span>
        <StatusPill status="queued" />
      </div>
      <div className="agent-card__body">
        <div className="agent-card__meta">
          <span><b>waiting on</b> group 3 capacity</span>
        </div>
        <div style={{ flex: 1, display: "grid", placeItems: "center", color: "var(--text-mute)", fontSize: 11, letterSpacing: "0.16em" }}>
          ┄ ┄ ┄ ┄ ┄ ┄ ┄ ┄
        </div>
      </div>
    </div>
  );
}

// ─────────── SIDE COLUMN: system log + group progress + signals
function SidePanels({ fakeNow, counts, elapsedMs }) {
  return (
    <div className="dash__side">
      <Panel id="SYS-03" title="ORCHESTRATOR LOG" flush right={<span>night_manager.log · tail</span>}>
        <div className="syslog">
          {NM_SYSTEM_LOG.slice().reverse().map((ln, i) => (
            <div key={i} className={`ln ln--${ln.level}`}>
              <span className="t">{ln.t}</span>
              <span className="lvl">{ln.level}</span>
              <span className="msg">{ln.msg}</span>
            </div>
          ))}
          <div className="ln ln--info">
            <span className="t">{fmtClock(fakeNow)}</span>
            <span className="lvl">live</span>
            <span className="msg cursor">polling state.json</span>
          </div>
        </div>
      </Panel>

      <Panel id="STG-04" title="STAGES" flush>
        <div className="syslog">
          <div className="ln ln--info">
            <span className="t">tests</span>
            <span className="lvl">running</span>
            <span className="msg dim">ABS-95 · pnpm test widgets</span>
          </div>
          <div className="ln ln--info">
            <span className="t">tests</span>
            <span className="lvl">running</span>
            <span className="msg dim">ABS-96 · pytest billing</span>
          </div>
          <div className="ln ln--info">
            <span className="t">read</span>
            <span className="lvl">running</span>
            <span className="msg dim">ABS-97 · scanning auth module</span>
          </div>
          <div className="ln ln--warn">
            <span className="t">review</span>
            <span className="lvl">v2</span>
            <span className="msg dim">ABS-94 · webhook dedupe</span>
          </div>
        </div>
      </Panel>

      <Panel id="SIG-05" title="SIGNALS" flush>
        <div style={{ padding: "12px 14px", display: "flex", flexDirection: "column", gap: 12 }}>
          <Signal label="LINEAR API" tone="ok" value="OK · 18ms" />
          <Signal label="GIT WORKTREE POOL" tone="ok" value={`${counts.in_progress + counts.reviewing}/10 in use`} />
          <Signal label="ANTHROPIC API" tone="ok" value="OK · 240ms" />
          <Signal label="PORT POOL" tone="ok" value="22 / 30 free" />
          <Signal label="DISK · /Users/op" tone="warn" value="62% · 314 GB free" />
          <Signal label="DEPLOY GATE" tone="queued" value="Group 4 pending" />
        </div>
      </Panel>
    </div>
  );
}

function Signal({ label, tone, value }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
      <Dot tone={tone} pulse={tone === "info"} />
      <span className="up" style={{ color: "var(--text-dim)", flex: 1, fontSize: 10 }}>{label}</span>
      <span className="mono" style={{ color: tone === "warn" ? "var(--warn)" : tone === "err" ? "var(--err)" : "var(--text)", fontSize: 11 }}>{value}</span>
    </div>
  );
}

Object.assign(window, { Dashboard });
