"use client";

import Link from "next/link";
import type { Issue } from "../lib/types";
import { STATUS_TONE } from "../lib/status";
import { fmtElapsed, fmtClock, parseISO } from "../lib/format";
import { useTick, useLogStream } from "../lib/hooks";
import { Panel } from "./panel";
import { Dot } from "./dot";
import { StatusPill } from "./status-pill";

export function IssueDetail({ issue }: { issue: Issue }) {
  const now = useTick(1000);
  const logEvents = useLogStream(issue.identifier);

  const startedMs = parseISO(issue.started_at);
  const completedMs = parseISO(issue.completed_at);
  const mergedMs = parseISO(issue.merged_at);
  const isLive =
    issue.status === "in_progress" || issue.status === "reviewing";
  const elapsedMs = isLive
    ? startedMs
      ? now - startedMs
      : null
    : completedMs && startedMs
      ? completedMs - startedMs
      : null;
  const tone = STATUS_TONE[issue.status];

  return (
    <div className="nm-col" style={{ gap: 12 }}>
      {/* Header strip */}
      <div className="nm-panel" style={{ padding: 0 }}>
        <span className="nm-tick-bl" />
        <span className="nm-tick-br" />
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "auto 1fr auto",
            alignItems: "stretch",
          }}
        >
          <div
            style={{
              padding: "16px 20px",
              borderRight: "1px solid var(--line)",
              display: "flex",
              flexDirection: "column",
              gap: 4,
              minWidth: 160,
            }}
          >
            <div className="nm-up">Issue</div>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <Dot tone={tone} pulse={isLive} />
              <span
                style={{
                  fontSize: 22,
                  color: "var(--primary)",
                  fontWeight: 600,
                  letterSpacing: "0.04em",
                }}
              >
                {issue.identifier}
              </span>
            </div>
            <StatusPill status={issue.status} />
          </div>
          <div
            style={{
              padding: "16px 20px",
              display: "flex",
              flexDirection: "column",
              gap: 6,
              minWidth: 0,
            }}
          >
            <div className="nm-up">Title</div>
            <div style={{ fontSize: 16, color: "var(--text)" }}>
              {issue.title}
            </div>
            <div
              className="nm-row"
              style={{ gap: 18, marginTop: 2, fontSize: 11, color: "var(--text-mute)" }}
            >
              <span>
                <b className="nm-dim">branch &#8250;</b>{" "}
                {issue.branch || "—"}
              </span>
              <span>
                <b className="nm-dim">worktree &#8250;</b>{" "}
                {issue.worktree || "—"}
              </span>
            </div>
          </div>
          <div
            style={{
              padding: "16px 20px",
              borderLeft: "1px solid var(--line)",
              display: "flex",
              gap: 18,
              alignItems: "center",
            }}
          >
            <Link href="/nm" className="nm-btn nm-btn--ghost" style={{ textDecoration: "none" }}>
              &larr; Back
            </Link>
            {isLive && (
              <>
                <ActionButton label="&#9654; RETRY" endpoint="retry" issueId={issue.identifier} />
                <ActionButton label="&#8631; SKIP" endpoint="skip" issueId={issue.identifier} />
                <ActionButton
                  label="&#9632; KILL"
                  endpoint={`/api/nm/agent/${issue.identifier}/kill`}
                  issueId={issue.identifier}
                  danger
                />
              </>
            )}
            {issue.status === "failed" && (
              <ActionButton label="&#9654; RETRY" endpoint="retry" issueId={issue.identifier} primary />
            )}
          </div>
        </div>
      </div>

      <div className="nm-issue">
        <div className="nm-issue__main">
          {issue.error && (
            <Panel id="ERR" title="ERROR" right={<span>last attempt</span>}>
              <div className="nm-errbox">
                <span className="nm-errbox__icn">!</span>
                <div>
                  <div
                    style={{
                      marginBottom: 6,
                      color: "var(--err)",
                      fontWeight: 600,
                      letterSpacing: "0.06em",
                      textTransform: "uppercase",
                    }}
                  >
                    E2E TEST FAILURE
                  </div>
                  <div>{issue.error}</div>
                  <div
                    style={{
                      marginTop: 12,
                      fontSize: 11,
                      color: "var(--text-mute)",
                    }}
                  >
                    attempts {issue.attempts} / 2 &middot; review attempts{" "}
                    {issue.review_attempts}
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
                {isLive && (
                  <span>
                    <span className="nm-dot nm-dot--info nm-dot--pulse" />{" "}
                    LIVE &middot;{" "}
                  </span>
                )}
                {logEvents.length} events
              </span>
            }
            flush
          >
            <div className="nm-fulllog">
              {logEvents.length === 0 && (
                <div
                  style={{
                    padding: 24,
                    color: "var(--text-mute)",
                    textAlign: "center",
                  }}
                >
                  {isLive
                    ? "waiting for agent events…"
                    : "no log events available"}
                </div>
              )}
              {logEvents.map((ev, i) => (
                <div
                  key={i}
                  className={`nm-fulllog__ev nm-fulllog__ev--${ev.kind}`}
                >
                  <span className="nm-fulllog__t">+{ev.t}</span>
                  {ev.kind === "tool" && (
                    <span>
                      <span className="nm-fulllog__name">{ev.name}</span>
                      <span className="nm-fulllog__args">{ev.args}</span>
                    </span>
                  )}
                  {ev.kind === "assistant" && (
                    <span className="nm-fulllog__txt">{ev.text}</span>
                  )}
                  {ev.kind === "review" && (
                    <span className="nm-fulllog__txt">{ev.text}</span>
                  )}
                </div>
              ))}
              {isLive && (
                <div className="nm-fulllog__ev">
                  <span className="nm-fulllog__t">live</span>
                  <span className="nm-prim nm-cursor" />
                </div>
              )}
            </div>
          </Panel>

          <Panel
            id="DIFF"
            title="WORKING DIFF"
            right={
              <span>
                HEAD &rarr; agent/{issue.identifier}
              </span>
            }
            flush
          >
            <div className="nm-diff">
              <div className="nm-diff__line nm-diff__line--ctx">
                <span className="nm-diff__gutter" />
                <span className="nm-mute">
                  diff preview available when agent is active
                </span>
              </div>
            </div>
          </Panel>
        </div>

        <div className="nm-issue__side">
          <Panel id="META" title="METADATA">
            <div className="nm-kv-grid">
              <span className="nm-kv-grid__k">Identifier</span>
              <span className="nm-kv-grid__v nm-prim">
                {issue.identifier}
              </span>
              <span className="nm-kv-grid__k">Status</span>
              <span className="nm-kv-grid__v">
                <StatusPill status={issue.status} />
              </span>
              <span className="nm-kv-grid__k">Attempts</span>
              <span className="nm-kv-grid__v">{issue.attempts}</span>
              <span className="nm-kv-grid__k">Reviews</span>
              <span className="nm-kv-grid__v">{issue.review_attempts}</span>
              <span className="nm-kv-grid__k">Branch</span>
              <span className="nm-kv-grid__v nm-truncate">
                {issue.branch || "—"}
              </span>
              <span className="nm-kv-grid__k">Worktree</span>
              <span
                className="nm-kv-grid__v nm-truncate"
                style={{ fontSize: 11 }}
              >
                {issue.worktree || "—"}
              </span>
              <span className="nm-kv-grid__k">PG Port</span>
              <span className="nm-kv-grid__v">
                {issue.ports?.pg ?? "—"}
              </span>
              <span className="nm-kv-grid__k">API Port</span>
              <span className="nm-kv-grid__v">
                {issue.ports?.api ?? "—"}
              </span>
              <span className="nm-kv-grid__k">Web Port</span>
              <span className="nm-kv-grid__v">
                {issue.ports?.web ?? "—"}
              </span>
              <span className="nm-kv-grid__k">PID</span>
              <span className="nm-kv-grid__v">
                {issue.pid ?? "—"}
              </span>
              {elapsedMs != null && (
                <>
                  <span className="nm-kv-grid__k">
                    {isLive ? "Elapsed" : "Duration"}
                  </span>
                  <span className="nm-kv-grid__v nm-prim">
                    {fmtElapsed(elapsedMs)}
                  </span>
                </>
              )}
            </div>
          </Panel>

          <Panel id="TIM" title="TIMELINE" flush>
            <div className="nm-timeline">
              <TimelineEvent t="planned" txt="Issue planned" done />
              {startedMs && (
                <TimelineEvent
                  t={fmtClock(startedMs)}
                  txt="Agent spawned"
                  done
                  tone="info"
                />
              )}
              {completedMs ? (
                <TimelineEvent
                  t={fmtClock(completedMs)}
                  txt="Coding complete"
                  done
                />
              ) : isLive ? (
                <TimelineEvent
                  t={fmtClock(now)}
                  txt="Coding in progress"
                  tone="info"
                  pulse
                />
              ) : (
                <TimelineEvent t="—" txt="Coding complete" pending />
              )}
              {issue.review_attempts > 0 && (
                <TimelineEvent
                  t={
                    completedMs
                      ? fmtClock(completedMs + 30000)
                      : "—"
                  }
                  txt={`Review pass ${issue.review_attempts}`}
                  done={issue.status !== "reviewing"}
                  tone={
                    issue.status === "reviewing"
                      ? "review"
                      : issue.status === "merged"
                        ? "ok"
                        : "err"
                  }
                  pulse={issue.status === "reviewing"}
                />
              )}
              {mergedMs ? (
                <TimelineEvent
                  t={fmtClock(mergedMs)}
                  txt="Merged to dev"
                  done
                  tone="ok"
                />
              ) : issue.status === "failed" ? (
                <TimelineEvent
                  t={completedMs ? fmtClock(completedMs) : "—"}
                  txt="Marked failed"
                  done
                  tone="err"
                />
              ) : (
                <TimelineEvent t="—" txt="Merge to dev" pending />
              )}
            </div>
          </Panel>

          <Panel id="ACT" title="ACTIONS" flush>
            <div
              style={{
                padding: 12,
                display: "flex",
                flexDirection: "column",
                gap: 8,
              }}
            >
              <ActionButton
                label="&#9654; Re-spawn agent"
                endpoint="retry"
                issueId={issue.identifier}
                fullWidth
              />
              <ActionButton
                label="&#8631; Mark as blocked"
                endpoint="skip"
                issueId={issue.identifier}
                fullWidth
              />
              <ActionButton
                label="&#9654; Open worktree in editor"
                endpoint="editor"
                issueId={issue.identifier}
                fullWidth
              />
              <ActionButton
                label="&#9654; Tail JSONL in terminal"
                endpoint="tail"
                issueId={issue.identifier}
                fullWidth
              />
              <ActionButton
                label="&#9632; SIGTERM agent process"
                endpoint={`/api/nm/agent/${issue.identifier}/kill`}
                issueId={issue.identifier}
                danger
                fullWidth
              />
            </div>
          </Panel>
        </div>
      </div>
    </div>
  );
}

function TimelineEvent({
  t,
  txt,
  done,
  pending,
  tone,
  pulse,
}: {
  t: string;
  txt: string;
  done?: boolean;
  pending?: boolean;
  tone?: string;
  pulse?: boolean;
}) {
  const dotColor = tone
    ? `var(--${tone})`
    : done
      ? "var(--text-dim)"
      : "var(--text-mute)";
  return (
    <div className="nm-timeline__ev">
      <span
        className="nm-timeline__dot"
        style={{
          background: pending ? "transparent" : dotColor,
          border: pending ? "1px dashed var(--text-mute)" : "none",
          animation: pulse ? "nm-pulse 1.4s ease-in-out infinite" : "none",
          boxShadow:
            !pending && tone ? `0 0 8px ${dotColor}` : "none",
        }}
      />
      <span className="nm-timeline__t">{t}</span>
      <span
        className="nm-timeline__txt"
        style={{ color: pending ? "var(--text-mute)" : "var(--text)" }}
      >
        {txt}
      </span>
    </div>
  );
}

function ActionButton({
  label,
  endpoint,
  issueId,
  primary,
  danger,
  fullWidth,
}: {
  label: string;
  endpoint: string;
  issueId: string;
  primary?: boolean;
  danger?: boolean;
  fullWidth?: boolean;
}) {
  const cls = `nm-btn ${primary ? "nm-btn--prim" : ""} ${danger ? "nm-btn--danger" : ""}`;

  async function handleClick() {
    if (endpoint.startsWith("/api/")) {
      try {
        await fetch(endpoint, { method: "POST" });
      } catch {
        // silent
      }
    } else {
      alert(`Action "${endpoint}" for ${issueId} — not yet implemented`);
    }
  }

  return (
    <button
      className={cls}
      onClick={handleClick}
      style={fullWidth ? { width: "100%", justifyContent: "center" } : undefined}
    >
      {label}
    </button>
  );
}
