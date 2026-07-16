// POST /api/billing/checkout/topup — proxy to POST /v1/billing/checkout/topup.
// Body: { sku }. Returns a Stripe Checkout URL the browser redirects to.
// Auth-required (ABS-381).

import { NextRequest } from "next/server";
import { callBackend } from "@/lib/api";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function POST(req: NextRequest) {
  const body = (await req.json().catch(() => null)) as Record<
    string,
    unknown
  > | null;
  if (!body || typeof body.sku !== "string") {
    return new Response(JSON.stringify({ error: "sku required" }), {
      status: 400,
      headers: { "Content-Type": "application/json" },
    });
  }
  const r = await callBackend("/v1/billing/checkout/topup", {
    method: "POST",
    body: { sku: body.sku },
  });
  const text = await r.text();
  return new Response(text, {
    status: r.status,
    headers: {
      "Content-Type":
        r.headers.get("Content-Type") || "application/json",
    },
  });
}
