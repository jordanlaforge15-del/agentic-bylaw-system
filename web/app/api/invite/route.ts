// POST /api/invite — accept a request-invite submission and insert
// it into the invite_request Postgres table. No auth: anyone on the
// public marketing site can submit. The /admin/invites page is the
// admin's review surface; approving from there is what actually
// grants access (via Clerk's allowlist).
//
// Replaces the previous JSONL-on-disk version. The JSONL file is no
// longer read or written; the file may still exist on the server but
// is ignored.

import { NextRequest, NextResponse } from "next/server";
import { createInvite } from "@/lib/invites";

export const runtime = "nodejs";

export async function POST(req: NextRequest) {
  const body = (await req.json().catch(() => null)) as Record<
    string,
    unknown
  > | null;
  if (!body) {
    return NextResponse.json({ error: "Invalid body" }, { status: 400 });
  }

  const email = typeof body.email === "string" ? body.email.trim() : "";
  const name = typeof body.name === "string" ? body.name.trim() : "";
  const role = typeof body.role === "string" ? body.role.trim() : "";
  const project = typeof body.project === "string" ? body.project.trim() : "";

  if (!email.includes("@") || email.length > 320) {
    return NextResponse.json({ error: "Invalid email" }, { status: 400 });
  }
  if (name.length === 0 || name.length > 200) {
    return NextResponse.json({ error: "Invalid name" }, { status: 400 });
  }
  if (project.length < 10 || project.length > 4000) {
    return NextResponse.json({ error: "Invalid project" }, { status: 400 });
  }

  const ip =
    req.headers.get("x-forwarded-for")?.split(",")[0]?.trim() ||
    req.headers.get("x-real-ip") ||
    null;
  const userAgent = req.headers.get("user-agent") || null;

  try {
    const { row, reused } = await createInvite({
      email,
      name,
      role: role || undefined,
      project,
      ip,
      userAgent: userAgent ? userAgent.slice(0, 500) : null,
    });
    // ``reused: true`` means the email already had a pending request.
    // We return success regardless — exposing "already submitted" to
    // a public form would let attackers enumerate the queue. The
    // admin page surfaces duplicates if it matters.
    return NextResponse.json({ id: row.id, reused });
  } catch (e) {
    console.error("invite create failed", e);
    return NextResponse.json(
      { error: "Could not record request" },
      { status: 500 },
    );
  }
}
