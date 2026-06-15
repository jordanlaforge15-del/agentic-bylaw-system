// POST /api/billing/questions/purchases/[id]/refine — proxy to
// POST /v1/billing/questions/purchases/{id}/refine. Serves one in-window
// follow-up (no extra charge). Body: { message }. Forwards the upstream
// 409 guardrail bodies verbatim so the client can route them:
//   - new_question     → a materially different question; buy a new answer
//   - window_exhausted  → follow-up budget / 24h window spent
//   - refinement_unavailable → nothing captured to refine
// (ABS-321 / ABS-317).

import { NextRequest } from "next/server";
import { callBackend } from "@/lib/api";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  const body = (await req.json().catch(() => null)) as Record<
    string,
    unknown
  > | null;
  if (!body || typeof body.message !== "string" || !body.message.trim()) {
    return new Response(JSON.stringify({ error: "message required" }), {
      status: 400,
      headers: { "Content-Type": "application/json" },
    });
  }
  const upstream = await callBackend(
    `/v1/billing/questions/purchases/${encodeURIComponent(id)}/refine`,
    { method: "POST", body: { message: body.message } },
  );
  const text = await upstream.text();
  return new Response(text, {
    status: upstream.status,
    headers: {
      "Content-Type":
        upstream.headers.get("Content-Type") || "application/json",
    },
  });
}
