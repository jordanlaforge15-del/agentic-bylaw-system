// POST /api/admin/invites/[id]/approve
//
// Admin-only. Flips a pending invite to approved by:
//   1. Calling Clerk's Backend API to add the email to the allowlist.
//   2. Updating the invite_request row with status='approved',
//      expires_at=now+14d, and the Clerk alid for later cleanup.
//
// Body (all optional — DB defaults apply when omitted):
//   {
//     "queryLimit": 100,
//     "monthlyInputTokens": 500000,
//     "monthlyOutputTokens": 100000,
//     "rpm": 6
//   }
//
// We do Clerk first then DB. If Clerk fails, no DB change. If Clerk
// succeeds and DB fails, we have an allowlist entry without a record
// — the admin can retry approve and addToAllowlist is idempotent on
// duplicate (we get back the existing alid).

import { NextRequest, NextResponse } from "next/server";
import { requireAdmin } from "@/lib/admin-auth";
import { addToAllowlist } from "@/lib/clerk-admin";
import { approveInvite, getInvite } from "@/lib/invites";

export const runtime = "nodejs";

export async function POST(
  req: NextRequest,
  ctx: { params: Promise<{ id: string }> },
) {
  const admin = await requireAdmin();
  if (!admin) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }
  const { id } = await ctx.params;

  const existing = await getInvite(id);
  if (!existing) {
    return NextResponse.json({ error: "Not found" }, { status: 404 });
  }
  if (existing.status !== "pending") {
    return NextResponse.json(
      { error: `Invite is ${existing.status}, not pending` },
      { status: 409 },
    );
  }

  const body = (await req.json().catch(() => ({}))) as Record<string, unknown>;
  const overrides = {
    queryLimit: toIntOr(body.queryLimit, undefined),
    monthlyInputTokens: toIntOr(body.monthlyInputTokens, undefined),
    monthlyOutputTokens: toIntOr(body.monthlyOutputTokens, undefined),
    rpm: toIntOr(body.rpm, undefined),
  };

  let alid: string;
  try {
    alid = await addToAllowlist(existing.email);
  } catch (e) {
    console.error("addToAllowlist failed for", existing.email, e);
    return NextResponse.json(
      { error: "Could not add to Clerk allowlist" },
      { status: 502 },
    );
  }

  const row = await approveInvite({
    id,
    decidedBy: admin.email,
    clerkAllowlistId: alid,
    ...overrides,
  });
  if (!row) {
    // Race: someone else approved/rejected between getInvite and now.
    return NextResponse.json(
      { error: "Invite changed status during approval" },
      { status: 409 },
    );
  }
  return NextResponse.json({ invite: row });
}

function toIntOr<T>(v: unknown, fallback: T): number | T {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string" && v.trim() !== "") {
    const n = Number(v);
    if (Number.isFinite(n)) return n;
  }
  return fallback;
}
