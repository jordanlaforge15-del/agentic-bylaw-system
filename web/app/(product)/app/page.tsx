// /app — server component gate.
//
// Server-side checks whether the signed-in user has accepted the
// current Terms and Conditions version. If not, redirects to
// /app/terms; otherwise renders the chat shell (the original 800-line
// client component, now living in chat-shell.tsx).
//
// Why the gate is here and not in the layout:
//
//   * /app/terms is a sibling route under the same (product) layout.
//     Putting the gate in the layout would redirect /app/terms back
//     to itself on every request (infinite loop).
//   * Page-level gating is the standard Next.js App Router pattern for
//     "this specific route requires X" — see the cases / billing
//     pages for the same shape elsewhere in the app.
//
// The acceptance check is a single GET to the FastAPI advisor — same
// proxy path (callBackend → ADVISOR_API_URL) the other server-side
// reads use. Fail-CLOSED: if the check can't confirm a current
// acceptance row (server error, network blip, anything other than a
// 2xx with ``accepted=true``), we redirect to /app/terms. Click-wrap
// enforceability is the whole point — fail-open would leak a bypass
// under intermittent backend errors. The terms page itself surfaces
// any "could not load" condition clearly to the user.

import { redirect } from "next/navigation";
import { callBackend } from "@/lib/api";
import ChatShell from "./chat-shell";

export const dynamic = "force-dynamic";

type TermsCheckResponse = {
  version: string;
  body: string;
  accepted: boolean;
};

export default async function ProductAppPage() {
  let accepted = false;
  try {
    const r = await callBackend("/v1/terms/current");
    if (r.ok) {
      const data = (await r.json()) as TermsCheckResponse;
      accepted = data.accepted === true;
    }
  } catch {
    // Treat as not-accepted — fail closed.
  }
  if (!accepted) {
    redirect("/app/terms");
  }
  return <ChatShell />;
}
