// Left pane of /app. Renders a single **case-aware** list that merges
// the current user's chat sessions (conversation product) with their
// priced "report" purchases (Answers product) so the two purchase types
// coexist legibly (ABS-345). Each row shows the anchor address in bold
// with the question/title muted beneath, the zone + timestamp in mono,
// an unread accent dot for rows you haven't opened, and a REPORT badge
// on report-backed rows. Selecting a conversation row calls
// `onSelect(id)`; selecting a report row opens it inside the unified /app
// workspace via `?report_id=` (ABS-344), keeping the shared panes.
//
// `refreshTrigger` is a number the page bumps after each successful
// chat turn — bumping it triggers a refetch so newly-created sessions
// (or updated titles after the first user message) appear without a
// page reload.

"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { UserButton, useUser } from "@clerk/nextjs";
import { Btn } from "@/components/btn";
import { Mono } from "@/components/mono";
import { cn } from "@/lib/cn";
import { GeneralFeedbackButton } from "@/components/product/general-feedback-button";

// Inlined at build time (NEXT_PUBLIC_*). When unset OR set to a
// placeholder (the example file ships "pk_test_replace-me"), we
// don't even touch Clerk's UserButton — the static "Halifax Studio"
// placeholder covers the dev path so devs running `npm run dev`
// against the X-Test-User-Id fallback still get a sensible footer.
const _PK = process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY ?? "";
const CLERK_ENABLED =
  /^pk_(test|live)_/.test(_PK) && _PK.length > 40 && !_PK.includes("replace");

type SessionSummary = {
  session_id: string;
  model: string;
  title: string;
  message_count: number;
  updated_at: string | null;
  case_id?: number | null;
  // ABS-345 discrete row pieces (older backends omit these; we fall
  // back to `title` when `anchor_label` is absent).
  anchor_label?: string | null;
  anchor_kind?: string | null;
  zone?: string | null;
  question?: string | null;
  kind?: string;
};

type ReportSummary = {
  id: number;
  question_slug: string;
  title: string;
  status: string;
  address?: string | null;
  zone?: string | null;
  answer_ready?: boolean;
  updated_at?: string | null;
};

// Unified sidebar row. `key` doubles as the stable id we persist in the
// client-side "seen" set that drives the unread dot.
type CaseRow = {
  key: string;
  kind: "conversation" | "report";
  sessionId: string | null;
  reportId: number | null;
  address: string | null;
  title: string;
  zone: string | null;
  updatedAt: string | null;
  // Whether the row has deliverable content worth an unread dot: a
  // conversation with at least one message, or a report whose answer
  // has been produced. Empty/pending rows never show a dot.
  hasContent: boolean;
  // ABS-343: a report whose generation job is still running (or queued).
  // The row shows a "generating…" affordance instead of the unread dot,
  // then flips to ready when the answer lands — so a user who left the
  // generation page can watch the case-list item resolve.
  generating: boolean;
};

// localStorage key for the set of rows the user has opened. Bumping the
// suffix invalidates the prior shape if the row-key scheme ever changes.
const SEEN_KEY = "abs-sidebar-seen-v1";

function loadSeen(): Set<string> {
  if (typeof window === "undefined") return new Set();
  try {
    const raw = window.localStorage.getItem(SEEN_KEY);
    if (!raw) return new Set();
    const parsed = JSON.parse(raw) as unknown;
    return Array.isArray(parsed) ? new Set(parsed.map(String)) : new Set();
  } catch {
    return new Set();
  }
}

function persistSeen(seen: Set<string>): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(SEEN_KEY, JSON.stringify([...seen]));
  } catch {
    // Private-mode / quota — unread state degrades to session-only.
  }
}

// Render a backend ISO timestamp as a sidebar-friendly relative label.
// Buckets: <60s "just now"; <60m "Nm"; <24h "Nh"; <7d "Nd"; older as
// "MMM D" (e.g. "May 6"). Returns "—" for null/unparseable inputs so
// freshly-minted sessions stay readable.
function formatRelative(iso: string | null): string {
  if (!iso) return "—";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "—";
  const diffSec = Math.max(0, (Date.now() - t) / 1000);
  if (diffSec < 60) return "just now";
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m`;
  if (diffSec < 86_400) return `${Math.floor(diffSec / 3600)}h`;
  if (diffSec < 7 * 86_400) return `${Math.floor(diffSec / 86_400)}d`;
  return new Date(t).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
  });
}

// Sort key: newest-first by updated_at, unparseable/absent timestamps last.
function sortByRecency(a: CaseRow, b: CaseRow): number {
  const ta = a.updatedAt ? Date.parse(a.updatedAt) : NaN;
  const tb = b.updatedAt ? Date.parse(b.updatedAt) : NaN;
  const va = Number.isNaN(ta) ? -Infinity : ta;
  const vb = Number.isNaN(tb) ? -Infinity : tb;
  return vb - va;
}

function sessionToRow(s: SessionSummary): CaseRow {
  const address = s.anchor_label?.trim() || null;
  // When we have a discrete anchor, the bold line is the address and the
  // muted line is the question. Without one, fall back to the composed
  // title (older backend) as the bold line.
  const title = address
    ? s.question?.trim() || ""
    : s.title?.trim() || "New reading";
  return {
    key: `session:${s.session_id}`,
    kind: "conversation",
    sessionId: s.session_id,
    reportId: null,
    address,
    title,
    zone: s.zone?.trim() || null,
    updatedAt: s.updated_at,
    hasContent: (s.message_count ?? 0) > 0,
    generating: false,
  };
}

function reportToRow(r: ReportSummary): CaseRow {
  const address = r.address?.trim() || null;
  return {
    key: `report:${r.id}`,
    kind: "report",
    sessionId: null,
    reportId: r.id,
    address,
    title: r.title?.trim() || "Report",
    zone: r.zone?.trim() || null,
    updatedAt: r.updated_at ?? null,
    hasContent: Boolean(r.answer_ready),
    // A purchased report is still "generating" while its engine job runs
    // (`generating`) or is queued (`authorized`) and no answer has landed.
    generating:
      !r.answer_ready &&
      (r.status === "generating" || r.status === "authorized"),
  };
}

type Props = {
  onNew: () => void;
  onSelect: (id: string) => void;
  activeSessionId: string | null;
  refreshTrigger: number;
  // When `true`, the sidebar drops the fixed width and right border —
  // the parent (Drawer) supplies them. Mobile uses this; desktop
  // renders the sidebar in its default in-flow shape.
  inDrawer?: boolean;
};

export function Sidebar({
  onNew,
  onSelect,
  activeSessionId,
  refreshTrigger,
  inDrawer,
}: Props) {
  const router = useRouter();
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [reports, setReports] = useState<ReportSummary[]>([]);
  const [q, setQ] = useState("");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [seen, setSeen] = useState<Set<string>>(() => loadSeen());

  useEffect(() => {
    let cancelled = false;
    (async () => {
      // Sessions (conversations) are the primary list; a failure here is
      // surfaced. Reports (Answers product) are best-effort — payments-off
      // deployments may not mount the endpoint — so a report-fetch failure
      // just leaves the list conversation-only rather than erroring out.
      try {
        const res = await fetch("/api/chat/sessions", { cache: "no-store" });
        if (!res.ok) {
          if (!cancelled)
            setLoadError(`Couldn't load your cases (HTTP ${res.status})`);
        } else {
          const data = (await res.json()) as { sessions: SessionSummary[] };
          if (!cancelled) {
            setSessions(data.sessions ?? []);
            setLoadError(null);
          }
        }
      } catch (e) {
        if (!cancelled)
          setLoadError(`Couldn't load your cases: ${(e as Error).message}`);
      }

      try {
        const res = await fetch("/api/billing/questions/purchases", {
          cache: "no-store",
        });
        if (res.ok) {
          const data = (await res.json()) as { reports: ReportSummary[] };
          if (!cancelled) setReports(data.reports ?? []);
        } else if (!cancelled) {
          setReports([]);
        }
      } catch {
        if (!cancelled) setReports([]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [refreshTrigger]);

  const markSeen = useCallback((key: string) => {
    setSeen((prev) => {
      if (prev.has(key)) return prev;
      const next = new Set(prev);
      next.add(key);
      persistSeen(next);
      return next;
    });
  }, []);

  // The active conversation is, by definition, open — clear its dot.
  useEffect(() => {
    if (activeSessionId) markSeen(`session:${activeSessionId}`);
  }, [activeSessionId, markSeen]);

  const rows = useMemo<CaseRow[]>(() => {
    const merged = [
      ...sessions.map(sessionToRow),
      ...reports.map(reportToRow),
    ];
    return merged.sort(sortByRecency);
  }, [sessions, reports]);

  const filtered = useMemo<CaseRow[]>(() => {
    if (!q) return rows;
    const needle = q.toLowerCase();
    return rows.filter((r) =>
      `${r.address ?? ""} ${r.title}`.toLowerCase().includes(needle),
    );
  }, [rows, q]);

  const handleActivate = (row: CaseRow) => {
    markSeen(row.key);
    if (row.kind === "report" && row.reportId !== null) {
      // ABS-344: open the report inside the unified /app workspace (shared
      // sidebar + parcel panes, Report·Conversation toggle) rather than the
      // standalone answer route.
      router.push(`/app?report_id=${row.reportId}`);
      return;
    }
    if (row.sessionId) onSelect(row.sessionId);
  };

  return (
    <aside
      className={
        inDrawer
          ? "bg-surface flex flex-col min-h-0 h-full w-full"
          : "border-r border-hair bg-surface flex flex-col min-h-0 w-[280px] flex-shrink-0"
      }
    >
      <div className="border-b border-hair p-4 flex flex-col gap-3">
        <Btn variant="primary" size="sm" onClick={onNew} className="w-full">
          + Open a case
        </Btn>
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search cases…"
          className="bg-surface-alt text-text border border-hair font-sans outline-none px-2.5 py-2"
          style={{ fontSize: 12.5 }}
        />
      </div>
      <div className="px-4 pt-3 pb-1.5">
        <Mono muted size={9.5}>
          RECENT · {filtered.length}
        </Mono>
      </div>
      <div className="flex-1 overflow-y-auto px-2 pb-3 flex flex-col gap-0.5">
        {loadError && (
          <div
            className="text-[12px] font-mono px-3 py-2"
            style={{ color: "var(--brick)" }}
          >
            {loadError}
          </div>
        )}
        {!loadError && filtered.length === 0 && (
          <div className="text-[12px] text-text-muted px-3 py-3 leading-[1.4]">
            {rows.length === 0
              ? "No cases yet. Open a case to start one."
              : "No matches."}
          </div>
        )}
        {filtered.map((row) => {
          const active =
            row.kind === "conversation" && row.sessionId === activeSessionId;
          const unread = row.hasContent && !active && !seen.has(row.key);
          const boldLine = row.address || row.title || "New reading";
          const mutedLine = row.address ? row.title : null;
          return (
            <button
              key={row.key}
              type="button"
              data-testid="case-row"
              data-kind={row.kind}
              onClick={() => handleActivate(row)}
              className={cn(
                "text-left flex flex-col gap-1 cursor-pointer text-text font-sans",
                "px-3 py-2.5 pl-3 transition-colors",
                active ? "bg-surface-alt" : "bg-transparent hover:bg-surface-alt",
              )}
              style={{
                borderLeft: active
                  ? "2px solid var(--accent)"
                  : "2px solid transparent",
              }}
            >
              <div className="flex justify-between items-start gap-2">
                <span
                  className="text-[12.5px] font-semibold tracking-[-0.005em] min-w-0"
                  style={{
                    display: "-webkit-box",
                    WebkitLineClamp: 2,
                    WebkitBoxOrient: "vertical",
                    overflow: "hidden",
                  }}
                >
                  {boldLine}
                </span>
                <div className="flex items-center gap-1.5 flex-shrink-0 pt-0.5">
                  {row.kind === "report" && (
                    <span
                      data-testid="report-badge"
                      className="font-mono uppercase leading-none px-1 py-[3px]"
                      style={{
                        fontSize: 8.5,
                        letterSpacing: "0.12em",
                        color: "var(--on-accent)",
                        background: "var(--accent)",
                      }}
                    >
                      Report
                    </span>
                  )}
                  {row.generating && (
                    <span
                      data-testid="row-generating"
                      className="flex items-center gap-1 font-mono uppercase leading-none"
                      style={{ fontSize: 8.5, letterSpacing: "0.1em" }}
                    >
                      <span
                        className="abs-pulse-dot bg-accent inline-block"
                        style={{ width: 5, height: 5, borderRadius: "50%" }}
                      />
                      <span className="text-text-muted">generating</span>
                    </span>
                  )}
                  {unread && !row.generating && (
                    <span
                      data-testid="unread-dot"
                      aria-label="Unread"
                      style={{
                        width: 6,
                        height: 6,
                        borderRadius: "50%",
                        background: "var(--accent)",
                      }}
                    />
                  )}
                </div>
              </div>
              {mutedLine && (
                <span className="text-[11.5px] text-text-muted leading-[1.35] line-clamp-2">
                  {mutedLine}
                </span>
              )}
              <div className="flex justify-between items-baseline mt-0.5 gap-2">
                <Mono muted size={9}>
                  {/* Zone when a chat turn has resolved it; a neutral
                      dash otherwise (the REPORT badge already conveys the
                      row's kind, so we don't repeat it here). */}
                  {row.zone || "—"}
                </Mono>
                <Mono muted size={9}>
                  {formatRelative(row.updatedAt)}
                </Mono>
              </div>
            </button>
          );
        })}
      </div>
      <div className="border-t border-hair px-4 py-2">
        <GeneralFeedbackButton />
      </div>
      <div className="border-t border-hair px-4 py-3 flex items-center gap-2.5">
        {CLERK_ENABLED ? <ClerkProfile /> : <PlaceholderProfile />}
      </div>
    </aside>
  );
}

// Sidebar footer in Clerk mode. Renders Clerk's UserButton (avatar +
// account / sign-out menu) once the SDK has hydrated; until then or
// for the rare not-signed-in case (proxy.ts redirects /app/* before
// it can mount, so this is mostly defensive) we fall back to the
// static placeholder.
function ClerkProfile() {
  const { isLoaded, isSignedIn, user } = useUser();
  if (!isLoaded || !isSignedIn) return <PlaceholderProfile />;
  const label =
    user?.primaryEmailAddress?.emailAddress ||
    user?.fullName ||
    user?.username ||
    "Signed in";
  return (
    <>
      <UserButton
        appearance={{
          elements: {
            rootBox: "flex items-center",
            avatarBox: "w-7 h-7 rounded-none",
          },
        }}
      />
      <div className="flex-1 min-w-0">
        <div className="text-[12.5px] font-semibold truncate">{label}</div>
        <div className="text-[10.5px] text-text-muted">
          Click avatar to manage or sign out
        </div>
      </div>
    </>
  );
}

// Static team-card stand-in shown when Clerk isn't configured (dev
// mode) or the user isn't signed in. Kept inline rather than promoted
// to its own file because nothing else renders it.
function PlaceholderProfile() {
  return (
    <>
      <div
        className="bg-text text-surface flex items-center justify-center font-mono font-semibold"
        style={{ width: 28, height: 28, fontSize: 11 }}
      >
        HS
      </div>
      <div className="flex-1 min-w-0">
        <div className="text-[12.5px] font-semibold">Halifax Studio</div>
        <div className="text-[10.5px] text-text-muted">Practice · 4 seats</div>
      </div>
      <button
        type="button"
        className="bg-transparent text-text-muted cursor-pointer font-mono"
        style={{ fontSize: 11 }}
        aria-label="Settings"
      >
        ⚙
      </button>
    </>
  );
}
