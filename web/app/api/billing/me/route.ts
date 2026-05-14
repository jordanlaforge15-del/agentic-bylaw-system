// GET /api/billing/me — proxy to GET /v1/billing/me. Returns the
// signed-in user's credit balance grouped by tier plus the dormant
// flag. Auth-required.

import { callBackend } from "@/lib/api";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET() {
  const r = await callBackend("/v1/billing/me");
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
