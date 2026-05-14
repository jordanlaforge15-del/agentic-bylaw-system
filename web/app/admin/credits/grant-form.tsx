// Client component: lookup-by-user-id, then render balance and a
// grant form. Hits backend admin endpoints via /api/admin proxies.

"use client";

import { useState } from "react";
import { Btn } from "@/components/btn";
import { Mono } from "@/components/mono";
import { TIER_DISPLAY, Tier } from "@/lib/cases";


type Balance = {
  tier: Tier;
  available: number;
  reserved: number;
  consumed: number;
};

type LookupResponse = {
  user_id: number;
  email: string;
  balances: Balance[];
};


const TIER_OPTIONS: Tier[] = ["quick", "standard", "complex"];


export function GrantCreditsForm() {
  const [userIdInput, setUserIdInput] = useState("");
  const [lookup, setLookup] = useState<LookupResponse | null>(null);
  const [tier, setTier] = useState<Tier>("standard");
  const [quantity, setQuantity] = useState("1");
  const [reason, setReason] = useState("");
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  async function lookupUser() {
    const id = Number(userIdInput);
    if (!Number.isInteger(id) || id <= 0) {
      setError("User id must be a positive integer.");
      return;
    }
    setWorking(true);
    setError(null);
    setSuccess(null);
    try {
      const r = await fetch(`/api/admin/users/${id}/credits`);
      if (!r.ok) {
        const detail = await r.text().catch(() => "");
        setError(`Lookup failed (${r.status}). ${detail.slice(0, 200)}`);
        setLookup(null);
        return;
      }
      setLookup((await r.json()) as LookupResponse);
    } finally {
      setWorking(false);
    }
  }

  async function grant() {
    if (!lookup) return;
    const qty = Number(quantity);
    if (!Number.isInteger(qty) || qty <= 0) {
      setError("Quantity must be a positive integer.");
      return;
    }
    if (!reason.trim()) {
      setError("Reason is required for the audit log.");
      return;
    }
    setWorking(true);
    setError(null);
    setSuccess(null);
    try {
      const r = await fetch(`/api/admin/users/${lookup.user_id}/credits`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tier, quantity: qty, reason }),
      });
      if (!r.ok) {
        const detail = await r.text().catch(() => "");
        setError(`Grant failed (${r.status}). ${detail.slice(0, 200)}`);
        return;
      }
      const data = (await r.json()) as { granted: number };
      setSuccess(`Granted ${data.granted} ${tier} credits.`);
      // Refresh the balance display.
      await lookupUser();
    } finally {
      setWorking(false);
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <section className="flex flex-col gap-2">
        <Mono size={11} muted>
          LOOK UP USER
        </Mono>
        <div className="flex gap-2">
          <input
            type="number"
            value={userIdInput}
            onChange={(e) => setUserIdInput(e.target.value)}
            placeholder="advisor_user.id (e.g. 17)"
            className="flex-1 bg-surface border border-hair px-3 py-2 text-[13.5px] font-mono"
          />
          <Btn variant="primary" size="sm" onClick={lookupUser} disabled={working}>
            {working ? "Loading…" : "Look up"}
          </Btn>
        </div>
      </section>

      {error && <div className="text-[13px] text-red-600">{error}</div>}
      {success && <div className="text-[13px] text-green-600">{success}</div>}

      {lookup && (
        <>
          <section className="flex flex-col gap-2">
            <Mono size={11} muted>
              USER #{lookup.user_id} · {lookup.email}
            </Mono>
            <div className="grid grid-cols-3 gap-3">
              {TIER_OPTIONS.map((t) => {
                const b = lookup.balances.find((x) => x.tier === t);
                return (
                  <div
                    key={t}
                    className="border border-hair p-4 flex flex-col gap-1"
                  >
                    <Mono size={10} muted>
                      {TIER_DISPLAY[t].toUpperCase()}
                    </Mono>
                    <div className="text-[24px] font-extrabold leading-none">
                      {b?.available ?? 0}
                    </div>
                    <div className="text-[11px] text-text-muted">
                      available · {b?.reserved ?? 0} in flight ·{" "}
                      {b?.consumed ?? 0} consumed
                    </div>
                  </div>
                );
              })}
            </div>
          </section>

          <section className="flex flex-col gap-3 border border-hair p-5">
            <Mono size={11} muted>
              GRANT CREDITS
            </Mono>
            <div className="grid grid-cols-3 gap-3">
              <label className="flex flex-col gap-1 text-[12px]">
                Tier
                <select
                  value={tier}
                  onChange={(e) => setTier(e.target.value as Tier)}
                  className="bg-surface border border-hair px-3 py-2 text-[13.5px]"
                >
                  {TIER_OPTIONS.map((t) => (
                    <option key={t} value={t}>
                      {TIER_DISPLAY[t]}
                    </option>
                  ))}
                </select>
              </label>
              <label className="flex flex-col gap-1 text-[12px]">
                Quantity
                <input
                  type="number"
                  min={1}
                  value={quantity}
                  onChange={(e) => setQuantity(e.target.value)}
                  className="bg-surface border border-hair px-3 py-2 text-[13.5px] font-mono"
                />
              </label>
              <label className="flex flex-col gap-1 text-[12px] col-span-3">
                Reason (audit log)
                <input
                  type="text"
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  placeholder="e.g. beta-tester gift, refund for cancelled session"
                  className="bg-surface border border-hair px-3 py-2 text-[13.5px]"
                />
              </label>
            </div>
            <div>
              <Btn variant="accent" size="sm" onClick={grant} disabled={working}>
                {working ? "Granting…" : "Grant credits"}
              </Btn>
            </div>
          </section>
        </>
      )}
    </div>
  );
}
