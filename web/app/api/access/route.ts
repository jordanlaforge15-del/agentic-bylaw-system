// POST /api/access — validate a shared password and set the matching
// access cookie. Two gates:
//
//   { gate: "demo",  password: <DEMO_PASSWORD>  } → sets abs_demo=1
//   { gate: "admin", password: <ADMIN_PASSWORD> } → sets abs_admin=1
//
// Cookies are httpOnly + sameSite=lax + 30-day max-age. `secure` flips
// on in production so the cookie won't leak over plain HTTP. If the
// gate is not configured (env var missing), we 503 — better to fail
// loud than to silently let everyone through.

import { NextRequest, NextResponse } from "next/server";

type Gate = "demo" | "admin";

const COOKIE: Record<Gate, string> = {
  demo: "abs_demo",
  admin: "abs_admin",
};

const ENV_VAR: Record<Gate, "DEMO_PASSWORD" | "ADMIN_PASSWORD"> = {
  demo: "DEMO_PASSWORD",
  admin: "ADMIN_PASSWORD",
};

export async function POST(req: NextRequest) {
  const body = (await req.json().catch(() => null)) as Record<
    string,
    unknown
  > | null;
  if (!body) {
    return NextResponse.json({ error: "Invalid body" }, { status: 400 });
  }

  const gate = body.gate;
  if (gate !== "demo" && gate !== "admin") {
    return NextResponse.json({ error: "Unknown gate" }, { status: 400 });
  }
  const expected = process.env[ENV_VAR[gate]];
  if (!expected) {
    return NextResponse.json(
      { error: `${ENV_VAR[gate]} not configured` },
      { status: 503 },
    );
  }
  const password = body.password;
  if (typeof password !== "string" || password !== expected) {
    return NextResponse.json({ error: "Wrong password" }, { status: 401 });
  }

  const res = NextResponse.json({ ok: true });
  res.cookies.set(COOKIE[gate], "1", {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    maxAge: 60 * 60 * 24 * 30,
    path: "/",
  });
  return res;
}
