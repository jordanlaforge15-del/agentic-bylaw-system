// POST /api/billing/questions/free-start — proxy to
// POST /v1/billing/questions/free-start.
//
// Payments-off path (ABS-320 / ABS-322): when billing is dormant the
// case-open form calls this instead of the Stripe checkout. The backend
// atomically decrements the user's free-question entitlement counter,
// opens (or finds) the case container, and returns the case_id so the
// browser can navigate straight to the in-app answer view.
//
// Returns 402 when the user's free-question counter is already 0.

import { NextRequest } from "next/server";
import { callBackend } from "@/lib/api";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function POST(req: NextRequest) {
  const body = (await req.json().catch(() => null)) as Record<
    string,
    unknown
  > | null;
  if (!body || typeof body.anchor_label !== "string") {
    return new Response(
      JSON.stringify({ error: "anchor_label required" }),
      {
        status: 400,
        headers: { "Content-Type": "application/json" },
      },
    );
  }
  const r = await callBackend("/v1/billing/questions/free-start", {
    method: "POST",
    body,
  });
  const text = await r.text();
  return new Response(text, {
    status: r.status,
    headers: {
      "Content-Type": r.headers.get("Content-Type") || "application/json",
    },
  });
}
