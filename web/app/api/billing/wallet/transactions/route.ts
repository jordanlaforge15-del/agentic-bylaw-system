// GET /api/billing/wallet/transactions — proxy to
// GET /v1/billing/wallet/transactions. Returns the signed-in user's token
// ledger newest-first, id-cursor paged. Forwards the ?limit and ?before_id
// query params through to the backend. Auth-required.

import { callBackend } from "@/lib/api";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  const limit = searchParams.get("limit") ?? undefined;
  const beforeId = searchParams.get("before_id") ?? undefined;

  const r = await callBackend("/v1/billing/wallet/transactions", {
    searchParams: { limit, before_id: beforeId },
  });
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
