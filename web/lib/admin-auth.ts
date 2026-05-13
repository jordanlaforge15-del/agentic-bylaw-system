// Server-side admin authentication for /admin/* routes.
//
// An admin is a signed-in Clerk user whose userId is in the
// ADVISOR_ADMIN_CLERK_USER_IDS env var (comma-separated). The list is
// short — typically one or two operator accounts — and lives in env
// rather than DB so admin access can't be edited from inside the app.
//
// Returns the admin's email when the caller is an admin, or null
// otherwise. Callers should treat null as a 401/redirect signal.
//
// Why not use the abs_admin cookie that the legacy fallback used:
// the cookie is a single shared password, can't distinguish between
// admins, and leaves no audit trail of who approved what. Clerk
// gives us a per-user identity that we can stamp on
// invite_request.decided_by.

import { auth, currentUser } from "@clerk/nextjs/server";

function adminUserIds(): string[] {
  const raw = process.env.ADVISOR_ADMIN_CLERK_USER_IDS || "";
  return raw
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

export type AdminContext = {
  userId: string;
  email: string;
};

// Returns the admin context, or null if the caller is not an
// authenticated admin. Cheap to call on every request — the auth
// session is already resolved by Clerk's middleware.
export async function requireAdmin(): Promise<AdminContext | null> {
  const session = await auth();
  if (!session.userId) return null;
  const allowed = adminUserIds();
  if (allowed.length === 0) {
    // Fail closed when the env var is missing — better than silently
    // letting anyone signed in into /admin.
    console.warn(
      "ADVISOR_ADMIN_CLERK_USER_IDS is not set; rejecting all admin access",
    );
    return null;
  }
  if (!allowed.includes(session.userId)) return null;
  const user = await currentUser();
  const email =
    user?.primaryEmailAddress?.emailAddress ||
    user?.emailAddresses?.[0]?.emailAddress ||
    session.userId; // last-resort fallback for the audit log
  return { userId: session.userId, email };
}
