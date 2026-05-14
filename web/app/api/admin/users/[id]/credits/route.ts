// GET  /api/admin/users/[id]/credits — balance lookup.
// POST /api/admin/users/[id]/credits — gift credits.
//
// Both gated by the backend's admin allowlist (Clerk userId in
// ADVISOR_ADMIN_CLERK_USER_IDS). The proxy doesn't re-check; the
// backend returns 403 when the caller isn't an admin.

import { NextRequest } from "next/server";
import { callBackend } from "@/lib/api";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(
  _req: NextRequest,
  ctx: { params: Promise<{ id: string }> },
) {
  const { id } = await ctx.params;
  const r = await callBackend(
    `/v1/admin/users/${encodeURIComponent(id)}/credits`,
  );
  const text = await r.text();
  return new Response(text, {
    status: r.status,
    headers: {
      "Content-Type":
        r.headers.get("Content-Type") || "application/json",
    },
  });
}

export async function POST(
  req: NextRequest,
  ctx: { params: Promise<{ id: string }> },
) {
  const { id } = await ctx.params;
  const body = (await req.json().catch(() => null)) as Record<
    string,
    unknown
  > | null;
  if (
    !body ||
    typeof body.tier !== "string" ||
    typeof body.quantity !== "number" ||
    typeof body.reason !== "string"
  ) {
    return new Response(
      JSON.stringify({ error: "tier, quantity, reason required" }),
      { status: 400, headers: { "Content-Type": "application/json" } },
    );
  }
  const r = await callBackend(
    `/v1/admin/users/${encodeURIComponent(id)}/credits`,
    {
      method: "POST",
      body: { tier: body.tier, quantity: body.quantity, reason: body.reason },
    },
  );
  const text = await r.text();
  return new Response(text, {
    status: r.status,
    headers: {
      "Content-Type":
        r.headers.get("Content-Type") || "application/json",
    },
  });
}
