// POST /api/billing/checkout/pack — proxy to POST /v1/billing/checkout/pack.
// Body: { tier, pack_sku }. Returns a Stripe Checkout URL the
// browser redirects to.

import { NextRequest } from "next/server";
import { callBackend } from "@/lib/api";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function POST(req: NextRequest) {
  const body = (await req.json().catch(() => null)) as Record<
    string,
    unknown
  > | null;
  if (
    !body ||
    typeof body.tier !== "string" ||
    typeof body.pack_sku !== "string"
  ) {
    return new Response(
      JSON.stringify({ error: "tier and pack_sku required" }),
      {
        status: 400,
        headers: { "Content-Type": "application/json" },
      },
    );
  }
  const r = await callBackend("/v1/billing/checkout/pack", {
    method: "POST",
    body: { tier: body.tier, pack_sku: body.pack_sku },
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
