// GET /api/cases/[id] — one case by id (proxy to GET /v1/cases/{id}).
//
// ABS-424: GET /api/cases is capped at the newest N cases, so it is not a
// reliable source for a specific case's `user_case_number`. The workspace
// footer needs that number on a direct `/app?case_id=N` load, otherwise the
// badge only settles when a chat turn's SSE `session` event arrives — which
// is what made "CASE #N" appear to change identity mid-conversation.

import { NextRequest } from "next/server";
import { callBackend } from "@/lib/api";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(
  _req: NextRequest,
  ctx: { params: Promise<{ id: string }> },
) {
  const { id } = await ctx.params;
  const r = await callBackend(`/v1/cases/${encodeURIComponent(id)}`);
  const text = await r.text();
  return new Response(text, {
    status: r.status,
    headers: {
      "Content-Type": r.headers.get("Content-Type") || "application/json",
    },
  });
}
