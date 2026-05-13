// Client table for /admin/invites. Renders each invite as a card
// with Approve / Reject actions for pending rows. Approve opens a
// small inline form so the admin can override the default caps
// before submitting.
//
// The page (server component) supplies the initial rows; this
// component does optimistic-then-re-render-on-success against the
// admin API routes. Errors bubble up as red banners on the row.

"use client";

import { useEffect, useState, useTransition } from "react";
import type { InviteRequestRow } from "@/lib/invites";
import { Mono } from "@/components/mono";
import { Btn } from "@/components/btn";

const DEFAULTS = {
  queryLimit: 100,
  monthlyInputTokens: 500_000,
  monthlyOutputTokens: 100_000,
  rpm: 6,
};

export function InvitesTable({
  initialInvites,
}: {
  initialInvites: InviteRequestRow[];
}) {
  const [invites, setInvites] = useState(initialInvites);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [rowError, setRowError] = useState<Record<string, string>>({});
  const [, startTransition] = useTransition();

  // Lazy sweep on mount: ask the server to clean up expired invites.
  // If anything was swept, refetch the list. Failure is logged but
  // doesn't block the page render.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch("/api/admin/invites/sweep-expired", {
          method: "POST",
        });
        if (!res.ok) return;
        const data = (await res.json()) as { processed: number };
        if (data.processed > 0 && !cancelled) {
          refetch();
        }
      } catch {
        // ignore — sweep is best-effort
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function refetch() {
    startTransition(async () => {
      const res = await fetch("/admin/invites?format=json", { cache: "no-store" });
      if (!res.ok) return;
      // The page is RSC; easier to just reload via router. But here
      // we already have a JSON-shaped response via a sibling route if
      // we want one — for now, a hard refresh suffices.
      window.location.reload();
    });
  }

  async function callAction(
    id: string,
    action: "approve" | "reject",
    body?: Record<string, unknown>,
  ) {
    setRowError((m) => ({ ...m, [id]: "" }));
    try {
      const res = await fetch(`/api/admin/invites/${id}/${action}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body ?? {}),
      });
      if (!res.ok) {
        const data = (await res.json().catch(() => ({}))) as { error?: string };
        setRowError((m) => ({
          ...m,
          [id]: data.error || `Request failed (HTTP ${res.status})`,
        }));
        return;
      }
      const data = (await res.json()) as {
        invite: InviteRequestRow;
        emailSent?: boolean;
        emailError?: string | null;
      };
      setInvites((prev) =>
        prev.map((inv) => (inv.id === id ? data.invite : inv)),
      );
      // Surface email-send failure on the row — invite is approved
      // but the user didn't get notified, so the admin needs to act
      // (copy the sign-in URL manually, or fix SMTP and retry).
      if (action === "approve" && data.emailSent === false) {
        setRowError((m) => ({
          ...m,
          [id]: `Approved, but approval email failed: ${data.emailError || "unknown"}`,
        }));
      }
      setExpandedId(null);
    } catch (e) {
      setRowError((m) => ({
        ...m,
        [id]: e instanceof Error ? e.message : "Network error",
      }));
    }
  }

  return (
    <ul className="flex flex-col gap-3 list-none p-0 m-0">
      {invites.map((inv) => (
        <InviteCard
          key={inv.id}
          invite={inv}
          expanded={expandedId === inv.id}
          onExpand={() => setExpandedId(inv.id)}
          onCollapse={() => setExpandedId(null)}
          error={rowError[inv.id] || null}
          onApprove={(overrides) => callAction(inv.id, "approve", overrides)}
          onReject={() => callAction(inv.id, "reject")}
        />
      ))}
    </ul>
  );
}

function InviteCard({
  invite,
  expanded,
  onExpand,
  onCollapse,
  onApprove,
  onReject,
  error,
}: {
  invite: InviteRequestRow;
  expanded: boolean;
  onExpand: () => void;
  onCollapse: () => void;
  onApprove: (overrides: Record<string, number>) => void;
  onReject: () => void;
  error: string | null;
}) {
  const isPending = invite.status === "pending";
  const statusColor: Record<string, string> = {
    pending: "var(--accent-ink)",
    approved: "#1f7a3a",
    rejected: "var(--brick)",
    expired: "var(--text-muted)",
  };

  return (
    <li className="bg-surface-alt border border-hair p-5 flex flex-col gap-3">
      <div className="flex justify-between items-baseline gap-3 flex-wrap">
        <div className="flex items-baseline gap-3">
          <Mono accent size={10}>
            #{invite.id}
          </Mono>
          <span
            className="text-[16px] font-semibold"
            style={{ letterSpacing: "-0.015em" }}
          >
            {invite.name}
          </span>
          {invite.role && (
            <Mono muted size={10}>
              {invite.role}
            </Mono>
          )}
        </div>
        <span
          className="text-[11px] font-mono uppercase tracking-[0.14em]"
          style={{ color: statusColor[invite.status] }}
        >
          {invite.status}
        </span>
      </div>

      <div className="flex items-center gap-2 text-[13px]">
        <Mono muted size={10}>
          EMAIL
        </Mono>
        <a
          href={`mailto:${invite.email}`}
          className="text-text underline underline-offset-2 font-mono text-[12.5px]"
        >
          {invite.email}
        </a>
      </div>

      {invite.project && (
        <div className="flex flex-col gap-1.5">
          <Mono muted size={10}>
            PROJECT
          </Mono>
          <p className="m-0 text-[13.5px] leading-[1.55] whitespace-pre-wrap">
            {invite.project}
          </p>
        </div>
      )}

      <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11.5px] text-text-muted font-mono pt-2 border-t border-hair">
        <span>submitted {fmt(invite.created_at)}</span>
        {invite.decided_at && (
          <span>
            {invite.status} {fmt(invite.decided_at)} by {invite.decided_by}
          </span>
        )}
        {invite.expires_at && invite.status === "approved" && (
          <span>expires {fmt(invite.expires_at)}</span>
        )}
        {invite.redeemed_at && <span>redeemed {fmt(invite.redeemed_at)}</span>}
      </div>

      {error && (
        <div
          className="text-[12.5px] border border-hair p-2"
          style={{ color: "var(--brick)" }}
        >
          {error}
        </div>
      )}

      {isPending && !expanded && (
        <div className="flex gap-2">
          <Btn variant="primary" size="sm" onClick={onExpand}>
            Approve →
          </Btn>
          <Btn variant="ghost" size="sm" onClick={onReject}>
            Reject
          </Btn>
        </div>
      )}

      {isPending && expanded && (
        <ApproveForm
          onSubmit={(overrides) => onApprove(overrides)}
          onCancel={onCollapse}
        />
      )}
    </li>
  );
}

function ApproveForm({
  onSubmit,
  onCancel,
}: {
  onSubmit: (overrides: Record<string, number>) => void;
  onCancel: () => void;
}) {
  const [q, setQ] = useState(String(DEFAULTS.queryLimit));
  const [tin, setTin] = useState(String(DEFAULTS.monthlyInputTokens));
  const [tout, setTout] = useState(String(DEFAULTS.monthlyOutputTokens));
  const [rpm, setRpm] = useState(String(DEFAULTS.rpm));
  return (
    <div className="flex flex-col gap-3 bg-surface border border-hair p-3">
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <CapInput label="QUERIES / MO" value={q} onChange={setQ} />
        <CapInput label="INPUT TOKENS / MO" value={tin} onChange={setTin} />
        <CapInput label="OUTPUT TOKENS / MO" value={tout} onChange={setTout} />
        <CapInput label="REQUESTS / MIN" value={rpm} onChange={setRpm} />
      </div>
      <div className="flex gap-2">
        <Btn
          variant="primary"
          size="sm"
          onClick={() =>
            onSubmit({
              queryLimit: parseInt(q, 10),
              monthlyInputTokens: parseInt(tin, 10),
              monthlyOutputTokens: parseInt(tout, 10),
              rpm: parseInt(rpm, 10),
            })
          }
        >
          Confirm approve
        </Btn>
        <Btn variant="ghost" size="sm" onClick={onCancel}>
          Cancel
        </Btn>
      </div>
    </div>
  );
}

function CapInput({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="font-mono uppercase text-[10px] tracking-[0.14em] text-text-muted">
        {label}
      </span>
      <input
        type="number"
        min={0}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="bg-surface border border-hair text-text font-mono text-[13px] px-2 py-1.5"
        style={{ borderRadius: 0 }}
      />
    </label>
  );
}

function fmt(iso: string): string {
  try {
    const d = new Date(iso);
    return (
      d.toISOString().slice(0, 10) +
      " · " +
      d.toISOString().slice(11, 16) +
      " UTC"
    );
  } catch {
    return iso;
  }
}
