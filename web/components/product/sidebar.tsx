// Left pane of /app. Fetches the current user's chat sessions from
// /api/chat/sessions, renders one button per session, and calls
// `onSelect(id)` when one is clicked. The active session gets a 2px
// accent left border + alt-surface background.
//
// `refreshTrigger` is a number the page bumps after each successful
// chat turn — bumping it triggers a refetch so newly-created sessions
// (or updated titles after the first user message) appear without a
// page reload.

"use client";

import { useEffect, useMemo, useState } from "react";
import { UserButton, useUser } from "@clerk/nextjs";
import { Btn } from "@/components/btn";
import { Mono } from "@/components/mono";
import { cn } from "@/lib/cn";

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
};

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
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [q, setQ] = useState("");
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch("/api/chat/sessions", { cache: "no-store" });
        if (!res.ok) {
          if (!cancelled)
            setLoadError(`Couldn't load sessions (HTTP ${res.status})`);
          return;
        }
        const data = (await res.json()) as { sessions: SessionSummary[] };
        if (!cancelled) {
          setSessions(data.sessions);
          setLoadError(null);
        }
      } catch (e) {
        if (!cancelled)
          setLoadError(`Couldn't load sessions: ${(e as Error).message}`);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [refreshTrigger]);

  const filtered = useMemo<SessionSummary[]>(
    () =>
      sessions.filter(
        (x) => !q || x.title.toLowerCase().includes(q.toLowerCase()),
      ),
    [sessions, q],
  );

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
          + New reading
        </Btn>
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search readings…"
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
            {sessions.length === 0
              ? "No readings yet. Ask a question to start one."
              : "No matches."}
          </div>
        )}
        {filtered.map((th) => {
          const active = th.session_id === activeSessionId;
          return (
            <button
              key={th.session_id}
              type="button"
              onClick={() => onSelect(th.session_id)}
              className={cn(
                "text-left flex flex-col gap-1 cursor-pointer text-text font-sans",
                "px-3 py-2.5 pl-3 transition-colors",
                active
                  ? "bg-surface-alt"
                  : "bg-transparent hover:bg-surface-alt",
              )}
              style={{
                borderLeft: active
                  ? "2px solid var(--accent)"
                  : "2px solid transparent",
              }}
            >
              <span
                className="text-[12.5px] font-semibold tracking-[-0.005em]"
                style={{
                  display: "-webkit-box",
                  WebkitLineClamp: 2,
                  WebkitBoxOrient: "vertical",
                  overflow: "hidden",
                }}
              >
                {th.title}
              </span>
              <div className="flex justify-between items-baseline mt-0.5">
                <Mono muted size={9}>
                  {th.message_count} MSG
                </Mono>
                <Mono muted size={9}>{formatRelative(th.updated_at)}</Mono>
              </div>
            </button>
          );
        })}
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
