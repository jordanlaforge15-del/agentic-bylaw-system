// POST /api/billing/questions/purchases/[id]/answer — proxy to
// POST /v1/billing/questions/purchases/{id}/answer. Runs the purchased
// question through the engine (idempotent: a settled purchase is
// returned unchanged) and returns the updated purchase + answer.
// No request body. Auth-required (ABS-321).

import { NextRequest } from "next/server";
import { callBackend } from "@/lib/api";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function POST(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  const upstream = await callBackend(
    `/v1/billing/questions/purchases/${encodeURIComponent(id)}/answer`,
    { method: "POST" },
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
