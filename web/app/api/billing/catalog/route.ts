// GET /api/billing/catalog — proxy to GET /v1/billing/catalog.
//
// Public endpoint (skipAuth=true) so the marketing /pricing page can
// render the price matrix for anonymous visitors. The backend marks
// each offer with `available: false` when the corresponding Stripe
// Price ID isn't configured, and the frontend disables the "Buy"
// button accordingly.

import { callBackend } from "@/lib/api";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET() {
  const r = await callBackend("/v1/billing/catalog", { skipAuth: true });
  const text = await r.text();
  return new Response(text, {
    status: r.status,
    headers: {
      "Content-Type":
        r.headers.get("Content-Type") || "application/json",
      "Cache-Control": "no-store",
    },
  });
}
