// GET /api/billing/questions — proxy to GET /v1/billing/questions.
//
// Public endpoint (skipAuth=true) so the question-menu page can render
// the priced-question launch menu for anonymous visitors. The backend
// marks each question with `available: false` when its Stripe Price ID
// isn't configured (or billing is dormant), and the frontend disables
// the "Buy" button accordingly.

import { callBackend } from "@/lib/api";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET() {
  const r = await callBackend("/v1/billing/questions", { skipAuth: true });
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
