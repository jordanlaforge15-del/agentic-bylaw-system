// GET /api/billing/questions/purchases — proxy to
// GET /v1/billing/questions/purchases. Returns the caller's priced
// "report" purchases (ABS-345) so the product sidebar can merge them
// with conversation sessions into one case-aware list. Auth-required;
// ownership is enforced upstream.

import { callBackend } from "@/lib/api";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET() {
  const upstream = await callBackend("/v1/billing/questions/purchases");
  const text = await upstream.text();
  return new Response(text, {
    status: upstream.status,
    headers: {
      "Content-Type":
        upstream.headers.get("Content-Type") || "application/json",
      "Cache-Control": "no-store",
    },
  });
}
