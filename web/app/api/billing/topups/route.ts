// GET /api/billing/topups — proxy to GET /v1/billing/topups.
//
// Public endpoint (skipAuth=true) so the marketing /pricing page can
// render the token top-up options for anonymous visitors. The backend
// marks each option `available: false` when payments are off or the
// corresponding Stripe Price ID isn't configured, and the frontend
// disables the "Buy" button accordingly (ABS-381).

import { callBackend } from "@/lib/api";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET() {
  const r = await callBackend("/v1/billing/topups", { skipAuth: true });
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
