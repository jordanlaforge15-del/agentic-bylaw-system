// /admin/credits — admin tooling to look up a user's credit balance
// and gift credits manually. The lookup form takes an internal user
// id; we use that (rather than email) because it's stable and the
// admin already has it from the cases page.

import { redirect } from "next/navigation";
import { requireAdmin } from "@/lib/admin-auth";
import { Mono } from "@/components/mono";
import { GrantCreditsForm } from "./grant-form";

export const dynamic = "force-dynamic";


export default async function AdminCreditsPage() {
  const admin = await requireAdmin();
  if (!admin) {
    redirect("/sign-in");
  }
  return (
    <div
      className="min-h-screen bg-surface text-text px-8 py-12 mx-auto"
      style={{ maxWidth: 900 }}
    >
      <header className="flex flex-col gap-3 pb-6 mb-8 border-b border-hair">
        <Mono muted size={11}>
          ADMIN · CREDITS · {admin.email}
        </Mono>
        <h1
          className="font-sans font-extrabold m-0"
          style={{ fontSize: 44, letterSpacing: "-0.035em", lineHeight: 1 }}
        >
          Credits
        </h1>
        <p className="text-text-muted text-[13.5px] max-w-[640px]">
          Look up a user&apos;s case-credit balance and gift credits.
          Grants are recorded as ``admin_credit_grant`` events for the
          audit trail.
        </p>
      </header>

      <GrantCreditsForm />
    </div>
  );
}
