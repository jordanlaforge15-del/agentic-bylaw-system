// POST /api/admin/invites/[id]/reject
//
// Admin-only. Marks a pending invite as rejected. No Clerk-side
// action — a rejected invite never made it onto the allowlist, so
// there's nothing to undo.

import { NextResponse } from "next/server";
import { requireAdmin } from "@/lib/admin-auth";
import { getInvite, rejectInvite } from "@/lib/invites";

export const runtime = "nodejs";

export async function POST(
  _req: Request,
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

  const row = await rejectInvite(id, admin.email);
  if (!row) {
    return NextResponse.json(
      { error: "Invite changed status during rejection" },
      { status: 409 },
    );
  }
  return NextResponse.json({ invite: row });
}
