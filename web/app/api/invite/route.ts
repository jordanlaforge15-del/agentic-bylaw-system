// POST /api/invite — accept a request-invite submission and append it
// to a local JSONL file. No auth: anyone on the public marketing site
// can submit. Read the resulting file with `cat web/data/invites.jsonl`
// or via the gated /admin/invites page.
//
// File-on-disk persistence is intentional for the demo phase. It works
// fine for `npm run dev` on a laptop or a single VM; if this app is
// ever moved to a serverless host without a persistent disk, swap the
// fs append for a real DB. The schema is the JSONL line itself.

import { NextRequest, NextResponse } from "next/server";
import { promises as fs } from "fs";
import path from "path";

const DATA_FILE = path.join(process.cwd(), "data", "invites.jsonl");

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

  const id = `ABS-${Math.floor(1000 + Math.random() * 9000)}`;
  const record = {
    id,
    email,
    name,
    role,
    project,
    createdAt: new Date().toISOString(),
    ip:
      req.headers.get("x-forwarded-for")?.split(",")[0]?.trim() ||
      req.headers.get("x-real-ip") ||
      null,
    userAgent: req.headers.get("user-agent") || null,
  };

  await fs.mkdir(path.dirname(DATA_FILE), { recursive: true });
  await fs.appendFile(DATA_FILE, JSON.stringify(record) + "\n", "utf8");

  return NextResponse.json({ id });
}
