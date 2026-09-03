// POST /api/billing/wallet/refill — proxy to POST /v1/billing/wallet/refill.
// Claims one self-serve beta refill (ABS-405): the way out of an overdrawn
// wallet while payments are off, replacing a manual operator grant.
//
// No request body — the claim is entirely policy-driven server-side (size,
// cooldown, lifetime cap), so there is nothing for the client to ask for.
// Auth-required; the backend scopes the claim to the caller's own wallet.
//
// Always relays the upstream status. A refused claim ("cooldown",
// "exhausted") comes back as a 200 with a reason, not an error.

import { callBackend } from "@/lib/api";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function POST() {
  const r = await callBackend("/v1/billing/wallet/refill", {
    method: "POST",
    body: {},
  });
  const text = await r.text();
  return new Response(text, {
    status: r.status,
    headers: {
      "Content-Type": r.headers.get("Content-Type") || "application/json",
      "Cache-Control": "no-store",
    },
  });
}
