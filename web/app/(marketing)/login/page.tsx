// Legacy /login route. The product moved to Clerk-hosted sign-in at
// /sign-in; this page exists only to keep external bookmarks and any
// in-flight email links from 404'ing during the cutover. The previous
// mock-auth form is gone — see git history if you need to revive it
// for a rollback.

import { redirect } from "next/navigation";

export default function LoginRedirect() {
  redirect("/sign-in");
}
