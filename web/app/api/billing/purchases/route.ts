// GET /api/billing/purchases — proxy to GET /v1/billing/purchases.
// Returns the user's pack-purchase history, newest-first, capped at
// 100 rows by the backend.

import { callBackend } from "@/lib/api";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET() {
  const r = await callBackend("/v1/billing/purchases");
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
