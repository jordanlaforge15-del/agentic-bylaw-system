// POST /api/admin/invites/sweep-expired
//
// Find approved invites past their 14-day expiry that haven't been
// redeemed, remove them from Clerk's allowlist, and mark the row
// expired. Idempotent — running it twice is a no-op the second time.
//
// Two invocation paths:
//   1. Admin-triggered from /admin/invites (button + automatic on
//      page load — see the page component).
//   2. Cron-triggered. POST with header
//      X-Sweep-Token: <CLERK_SWEEP_TOKEN> bypasses the admin auth
//      check so a cron job can call it without a logged-in admin
//      session. Set CLERK_SWEEP_TOKEN to a random string in env if
//      you want to use this path; absent token means the admin
//      session check is the only entry.

import { NextRequest, NextResponse } from "next/server";
import { requireAdmin } from "@/lib/admin-auth";
import { removeFromAllowlist } from "@/lib/clerk-admin";
import {
  findExpiredApprovedInvites,
  markInviteExpired,
} from "@/lib/invites";

export const runtime = "nodejs";

export async function POST(req: NextRequest) {
  // Either an admin session OR a valid sweep-token header. Both
  // unauthorized → 401.
  const sweepToken = process.env.CLERK_SWEEP_TOKEN;
  const headerToken = req.headers.get("x-sweep-token") || "";
  const tokenOk = !!sweepToken && headerToken === sweepToken;
  let actor = "cron";
  if (!tokenOk) {
    const admin = await requireAdmin();
    if (!admin) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }
    actor = admin.email;
  }

  const expired = await findExpiredApprovedInvites();
  const results: Array<{
    id: string;
    email: string;
    ok: boolean;
    error?: string;
  }> = [];

  for (const row of expired) {
    try {
      if (row.clerk_allowlist_id) {
        await removeFromAllowlist(row.clerk_allowlist_id);
      }
      await markInviteExpired(row.id);
      results.push({ id: row.id, email: row.email, ok: true });
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      console.error("sweep-expired failed for", row.id, msg);
      results.push({ id: row.id, email: row.email, ok: false, error: msg });
    }
  }

  console.log(
    `sweep-expired (actor=${actor}): processed ${results.length} ` +
      `(${results.filter((r) => r.ok).length} ok, ` +
      `${results.filter((r) => !r.ok).length} failed)`,
  );
  return NextResponse.json({ processed: results.length, results });
}
