"use client";

import { useMemo } from "react";
import Link from "next/link";
import type { RunState, Issue, Group, SystemLogEntry } from "../lib/types";
import { STATUS_LABEL, STATUS_TONE, GROUP_TONE } from "../lib/status";
import type { GroupState } from "../lib/status";
import { fmtElapsed, fmtMinutes, fmtClock, parseISO } from "../lib/format";
import { useTick, useOrchestratorLog } from "../lib/hooks";
import { Panel } from "./panel";
import { Dot } from "./dot";
import { StatusPill } from "./status-pill";
import { KPI } from "./kpi";
import { AccessLogPanel } from "./access-log-panel";

export function Dashboard({ state }: { state: RunState }) {
  const now = useTick(1000);
  const orchestratorLog = useOrchestratorLog();
  const all = Object.values(state.issues);
  const counts = useMemo(() => {
    const c: Record<string, number> = {
      queued: 0,
      in_progress: 0,
      reviewing: 0,
      merged: 0,
      failed: 0,
      blocked: 0,
    };
    all.forEach((i) => {
      c[i.status] = (c[i.status] || 0) + 1;
    });
    return c;
  }, [all]);

  const startedMs = new Date(state.started_at).getTime();
  const elapsedMs = now ? now - startedMs : 0;
  const total = all.length;
  const processed = counts.merged + counts.failed + counts.blocked;
  const remaining = total - processed;
  const avgMs = processed > 0 ? elapsedMs / processed : 0;
  const etaMs = avgMs * remaining;

  return (
    <div className="nm-col" style={{ gap: 12 }}>
      <div className="nm-kpis">
        <KPI label="Elapsed" value={fmtElapsed(elapsedMs)} tone="prim" />
        <KPI
          label="Agents Active"
          value={counts.in_progress + counts.reviewing}
          unit={`/ ${state.config.max_agents}`}
          tone="prim"
        />
        <KPI label="Merged" value={counts.merged} unit={`/ ${total}`} tone="ok" />
        <KPI
          label="Failed"
          value={counts.failed}
          tone={counts.failed > 0 ? "err" : undefined}
        />
        <KPI label="Queued" value={counts.queued} />
        <KPI
          label="ETA"
          value={fmtElapsed(etaMs)}
          hint={`avg ${fmtMinutes(processed > 0 ? elapsedMs / processed : 0)} / issue`}
        />
      </div>

      <div className="nm-dash">
        <Panel
          id="PLN-01"
          title="EXECUTION PLAN"
          right={
            <span>
              {state.plan.length} groups &middot; {total} issues
            </span>
          }
          flush
          className="nm-dash__plan"
        >
          {state.plan.map((group, idx) => (
            <PlanRow
              key={idx}
              group={group}
              groupNum={idx + 1}
              issues={state.issues}
            />
          ))}
        </Panel>

        <Panel
          id="OPS-02"
          title="ACTIVE AGENTS"
          right={
            <span>
              <span className="nm-dot nm-dot--info nm-dot--pulse" /> LIVE
              &middot; POLL 2s
            </span>
          }
          flush
          className="nm-dash__agents"
        >
          <div className="nm-agents-grid">
            {all
              .filter(
                (i) =>
                  i.status === "in_progress" || i.status === "reviewing",
              )
              .map((issue) => (
                <AgentCard key={issue.identifier} issue={issue} now={now} />
              ))}
            {all
              .filter((i) => i.status === "queued")
              .slice(0, 2)
              .map((issue) => (
                <QueuedSlot key={issue.identifier} issue={issue} />
              ))}
          </div>
        </Panel>

        <SidePanels
          now={now}
          counts={counts}
          state={state}
          orchestratorLog={orchestratorLog}
        />
      </div>
    </div>
  );
}

function PlanRow({
  group,
  groupNum,
  issues,
}: {
  group: Group;
  groupNum: number;
  issues: Record<string, Issue>;
}) {
  const slots = group.parallel
    .map((id) => issues[id])
    .filter(Boolean);
  const merged = slots.filter((i) => i.status === "merged").length;
  const running = slots.filter(
    (i) => i.status === "in_progress" || i.status === "reviewing",
  ).length;
  const failed = slots.filter((i) => i.status === "failed").length;
  const queued = slots.filter((i) => i.status === "queued").length;

  const padded: (Issue | null)[] = [...slots];
  while (padded.length < 4 && !group.deploy) padded.push(null);

  const groupState: GroupState = group.deploy
    ? "deploy"
    : slots.length === 0
      ? "queued"
      : slots.every((s) => s.status === "merged")
        ? "complete"
        : slots.some(
              (s) =>
                s.status === "in_progress" || s.status === "reviewing",
            )
          ? "active"
          : slots.every((s) => s.status === "queued")
            ? "queued"
            : "mixed";

  const groupTone = GROUP_TONE[groupState];

  return (
    <div
      className={`nm-plan-row ${group.deploy ? "nm-plan-row--deploy" : ""}`}
    >
      <div className="nm-plan-row__label">
        <span className="nm-plan-row__tag">GROUP</span>
        <span className="nm-plan-row__name">
          G{String(groupNum).padStart(2, "0")}
        </span>
        <span className="nm-up" style={{ marginTop: 4 }}>
          <Dot tone={groupTone} pulse={groupState === "active"} />{" "}
          {groupState}
        </span>
      </div>

      <div className="nm-plan-row__slots">
        {group.deploy ? (
          <div className="nm-deploy-card">
            <span className="nm-prim">&#9650;</span>
            DEPLOY &middot; PROMOTE DEV &rarr; STAGING &rarr; PROD
            <span className="nm-mute">
              awaiting groups 1&ndash;{groupNum - 1}
            </span>
          </div>
        ) : (
          padded.map((it, i) =>
            it ? (
              <PlanSlot key={it.identifier} issue={it} />
            ) : (
              <div
                key={`empty-${i}`}
                className="nm-plan-slot nm-plan-slot--empty"
              >
                &middot;
              </div>
            ),
          )
        )}
      </div>

      <div className="nm-plan-row__sum">
        {!group.deploy ? (
          <>
            <div className="nm-display" style={{ fontSize: 15 }}>
              {merged}/{slots.length}
            </div>
            <div className="nm-up" style={{ textAlign: "right" }}>
              {running > 0 && (
                <span>
                  <span className="nm-info">&#9679;</span> {running} run
                  &middot;{" "}
                </span>
              )}
              {failed > 0 && (
                <span>
                  <span className="nm-err">&#9679;</span> {failed} fail
                  &middot;{" "}
                </span>
              )}
              {queued > 0 && (
                <span>
                  <span className="nm-mute">&#9679;</span> {queued} q &middot;{" "}
                </span>
              )}
              {merged > 0 && (
                <span>
                  <span className="nm-ok">&#9679;</span> {merged} ok
                </span>
              )}
            </div>
          </>
        ) : (
          <span className="nm-up">queued</span>
        )}
      </div>
    </div>
  );
}

function PlanSlot({ issue }: { issue: Issue }) {
  const tone = STATUS_TONE[issue.status];
  const slotClass =
    issue.status === "in_progress" ? "running" : issue.status;
  return (
    <Link
      href={`/nm/issues/${issue.identifier}`}
      className={`nm-plan-slot nm-plan-slot--${slotClass}`}
      style={{ textDecoration: "none", color: "inherit" }}
    >
      <div className="nm-plan-slot__head">
        <Dot tone={tone} pulse={issue.status === "in_progress"} />
        <span className="nm-plan-slot__id">{issue.identifier}</span>
        <span
          className="nm-up nm-right"
          style={{ color: `var(--${tone})` }}
        >
          {STATUS_LABEL[issue.status]}
        </span>
      </div>
      <div className="nm-plan-slot__title">{issue.title}</div>
      <div className="nm-plan-slot__foot">
        <span>attempt {issue.attempts || 0}</span>
        {issue.ports && <span className="nm-right">:{issue.ports.web}</span>}
      </div>
    </Link>
  );
}

function AgentCard({ issue, now }: { issue: Issue; now: number }) {
  const startedMs = parseISO(issue.started_at);
  const elapsedMs = startedMs && now ? now - startedMs : 0;
  const tone = STATUS_TONE[issue.status];

  return (
    <Link
      href={`/nm/issues/${issue.identifier}`}
      className="nm-agent-card"
      style={{ cursor: "pointer", textDecoration: "none", color: "inherit" }}
    >
      <div className="nm-agent-card__head">
        <Dot tone={tone} pulse />
        <span className="nm-agent-card__id">{issue.identifier}</span>
        <span className="nm-agent-card__title">{issue.title}</span>
        <StatusPill status={issue.status} />
      </div>
      <div className="nm-agent-card__body">
        <div className="nm-agent-card__meta">
          <span>
            <b>elapsed</b>{" "}
            <span className="nm-prim" suppressHydrationWarning>
              {fmtElapsed(elapsedMs)}
            </span>
          </span>
          <span>
            <b>attempt</b> {issue.attempts}
            {issue.review_attempts > 0 && ` · rev ${issue.review_attempts}`}
          </span>
          <span>
            <b>branch</b>{" "}
            <span
              className="nm-truncate"
              style={{ display: "inline-block", maxWidth: 130 }}
            >
              {issue.branch?.replace("agent/", "") || "—"}
            </span>
          </span>
          <span>
            <b>ports</b> {issue.ports ? `${issue.ports.web}/${issue.ports.api}` : "—"}
          </span>
        </div>

        {issue.pid > 0 && (
          <div className="nm-agent-card__current">
            <div className="nm-agent-card__current-lbl">
              PID {issue.pid}
            </div>
          </div>
        )}

        <div className="nm-minilog">
          <div className="nm-minilog__line">
            <span className="nm-minilog__t">&middot;</span>
            <span className="nm-prim nm-cursor" />
          </div>
        </div>
      </div>
    </Link>
  );
}

function QueuedSlot({ issue }: { issue: Issue }) {
  return (
    <div className="nm-agent-card" style={{ opacity: 0.5, cursor: "default" }}>
      <div className="nm-agent-card__head">
        <Dot tone="queued" />
        <span className="nm-agent-card__id" style={{ color: "var(--text-mute)" }}>
          {issue.identifier}
        </span>
        <span className="nm-agent-card__title">{issue.title}</span>
        <StatusPill status="queued" />
      </div>
      <div className="nm-agent-card__body">
        <div className="nm-agent-card__meta">
          <span>
            <b>waiting on</b> group capacity
          </span>
        </div>
        <div
          style={{
            flex: 1,
            display: "grid",
            placeItems: "center",
            color: "var(--text-mute)",
            fontSize: 11,
            letterSpacing: "0.16em",
          }}
        >
          &#9476; &#9476; &#9476; &#9476; &#9476; &#9476; &#9476; &#9476;
        </div>
      </div>
    </div>
  );
}

function SidePanels({
  now,
  counts,
  state,
  orchestratorLog,
}: {
  now: number;
  counts: Record<string, number>;
  state: RunState;
  orchestratorLog: SystemLogEntry[];
}) {
  const activeWorktrees = Object.values(state.issues).filter(
    (i) => i.worktree,
  ).length;

  return (
    <div className="nm-dash__side">
      <Panel
        id="SYS-03"
        title="ORCHESTRATOR LOG"
        flush
        right={<span>night_manager.log &middot; tail</span>}
      >
        <div className="nm-syslog">
          <div className="nm-syslog__ln nm-syslog__ln--info">
            <span className="nm-syslog__t" suppressHydrationWarning>
              {now ? fmtClock(now) : "--:--:--"}
            </span>
            <span className="nm-syslog__lvl">live</span>
            <span className="nm-syslog__msg nm-cursor">
              polling state.json
            </span>
          </div>
          {orchestratorLog
            .slice()
            .reverse()
            .slice(0, 20)
            .map((ln, i) => (
              <div
                key={i}
                className={`nm-syslog__ln nm-syslog__ln--${ln.level}`}
              >
                <span className="nm-syslog__t">{ln.t}</span>
                <span className="nm-syslog__lvl">{ln.level}</span>
                <span className="nm-syslog__msg">{ln.msg}</span>
              </div>
            ))}
        </div>
      </Panel>

      <AccessLogPanel />

      <Panel id="SIG-05" title="SIGNALS" flush>
        <div
          style={{
            padding: "12px 14px",
            display: "flex",
            flexDirection: "column",
            gap: 12,
          }}
        >
          <Signal
            label="GIT WORKTREE POOL"
            tone="ok"
            value={`${activeWorktrees} active`}
          />
          <Signal
            label="MAX AGENTS"
            tone="ok"
            value={`${state.config.max_agents}`}
          />
          <Signal
            label="MODEL"
            tone="ok"
            value={state.config.model.toUpperCase()}
          />
          <Signal
            label="DEPLOY GATE"
            tone={state.config.deploy ? "info" : "queued"}
            value={state.config.deploy ? "armed" : "off"}
          />
          <Signal
            label="ISSUES"
            tone={
              counts.failed > 0
                ? "warn"
                : counts.in_progress > 0
                  ? "info"
                  : "ok"
            }
            value={`${counts.merged}m ${counts.failed}f ${counts.in_progress + counts.reviewing}r ${counts.queued}q`}
          />
        </div>
      </Panel>
    </div>
  );
}

function Signal({
  label,
  tone,
  value,
}: {
  label: string;
  tone: string;
  value: string;
}) {
  return (
    <div className="nm-signal">
      <Dot tone={tone} pulse={tone === "info"} />
      <span className="nm-signal__lbl">{label}</span>
      <span
        className="nm-signal__val"
        style={{
          color:
            tone === "warn"
              ? "var(--warn)"
              : tone === "err"
                ? "var(--err)"
                : "var(--text)",
        }}
      >
        {value}
      </span>
    </div>
  );
}
