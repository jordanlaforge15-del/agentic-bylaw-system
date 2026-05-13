// /admin/invites — list invite requests from Postgres and let admins
// approve / reject them. Approve calls Clerk's Backend API to add
// the email to the allowlist; reject just marks the row.
//
// Gated by proxy.ts: only Clerk users whose userId is in
// ADVISOR_ADMIN_CLERK_USER_IDS can reach this page. We re-check on
// the server here as defense-in-depth.

import { redirect } from "next/navigation";
import { requireAdmin } from "@/lib/admin-auth";
import { listInvites, type InviteRequestRow } from "@/lib/invites";
import { Mono } from "@/components/mono";
import { InvitesTable } from "./table";

export const dynamic = "force-dynamic";

export default async function AdminInvitesPage() {
  const admin = await requireAdmin();
  if (!admin) {
    redirect("/sign-in");
  }
  const invites = await listInvites();
  const counts = countByStatus(invites);

  return (
    <div
      className="min-h-screen bg-surface text-text px-8 py-12 mx-auto"
      style={{ maxWidth: 1100 }}
    >
      <header className="flex flex-col gap-3 pb-6 mb-8 border-b border-hair">
        <Mono muted size={11}>
          ADMIN · INVITE REQUESTS · {admin.email}
        </Mono>
        <h1
          className="font-sans font-extrabold m-0"
          style={{
            fontSize: 44,
            letterSpacing: "-0.035em",
            lineHeight: 1,
          }}
        >
          Invite requests.
        </h1>
        <p className="text-[14px] text-text-muted leading-[1.5] m-0">
          {invites.length === 0
            ? "No requests captured yet."
            : `${counts.pending} pending · ${counts.approved} approved · ${counts.rejected} rejected · ${counts.expired} expired`}
        </p>
      </header>

      {invites.length === 0 ? (
        <div className="bg-surface-alt border border-hair p-8 text-[13.5px] text-text-muted">
          Submit a request at{" "}
          <a
            href="/signup"
            className="text-text underline underline-offset-2"
          >
            /signup
          </a>{" "}
          to test the pipeline.
        </div>
      ) : (
        <InvitesTable initialInvites={invites} />
      )}
    </div>
  );
}

function countByStatus(invites: InviteRequestRow[]) {
  const counts = { pending: 0, approved: 0, rejected: 0, expired: 0 };
  for (const inv of invites) counts[inv.status] += 1;
  return counts;
}
