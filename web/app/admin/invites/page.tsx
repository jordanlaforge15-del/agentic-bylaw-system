// /admin/invites — read web/data/invites.jsonl and render every
// captured request, newest first. Gated by middleware (abs_admin
// cookie). "Approving" someone right now means: copy their email,
// share the DEMO_PASSWORD with them out-of-band. There is no per-
// invite approval state — that's deliberate, this page is the smallest
// viable inbox.

import { promises as fs } from "fs";
import path from "path";
import { Mono } from "@/components/mono";

export const dynamic = "force-dynamic";

type InviteRecord = {
  id: string;
  email: string;
  name: string;
  role: string;
  project: string;
  createdAt: string;
  ip: string | null;
  userAgent: string | null;
};

const DATA_FILE = path.join(process.cwd(), "data", "invites.jsonl");

async function loadInvites(): Promise<InviteRecord[]> {
  try {
    const raw = await fs.readFile(DATA_FILE, "utf8");
    return raw
      .split("\n")
      .filter(Boolean)
      .map((line) => JSON.parse(line) as InviteRecord)
      .reverse();
  } catch (e) {
    if ((e as NodeJS.ErrnoException).code === "ENOENT") return [];
    throw e;
  }
}

export default async function AdminInvitesPage() {
  const invites = await loadInvites();

  return (
    <div
      className="min-h-screen bg-surface text-text px-8 py-12 mx-auto"
      style={{ maxWidth: 1100 }}
    >
      <header className="flex flex-col gap-3 pb-6 mb-8 border-b border-hair">
        <Mono muted size={11}>
          ADMIN · INVITE REQUESTS
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
            : `${invites.length} request${invites.length === 1 ? "" : "s"} captured. To approve someone, share the DEMO_PASSWORD with them out-of-band.`}
        </p>
      </header>

      {invites.length === 0 ? (
        <div
          className="bg-surface-alt border border-hair p-8 text-[13.5px] text-text-muted"
        >
          Submit a request at{" "}
          <a
            href="/signup"
            className="text-text underline underline-offset-2"
          >
            /signup
          </a>{" "}
          to test the pipeline. Each row is one append to{" "}
          <code className="font-mono text-[12.5px]">
            web/data/invites.jsonl
          </code>
          .
        </div>
      ) : (
        <ul className="flex flex-col gap-3 list-none p-0 m-0">
          {invites.map((inv) => (
            <li
              key={`${inv.id}-${inv.createdAt}`}
              className="bg-surface-alt border border-hair p-5 flex flex-col gap-3"
            >
              <div className="flex justify-between items-baseline gap-3 flex-wrap">
                <div className="flex items-baseline gap-3">
                  <Mono accent size={10}>
                    #{inv.id}
                  </Mono>
                  <span
                    className="text-[16px] font-semibold"
                    style={{ letterSpacing: "-0.015em" }}
                  >
                    {inv.name}
                  </span>
                  <Mono muted size={10}>
                    {inv.role}
                  </Mono>
                </div>
                <Mono muted size={10}>
                  {formatDate(inv.createdAt)}
                </Mono>
              </div>

              <div className="flex items-center gap-2 text-[13px]">
                <Mono muted size={10}>
                  EMAIL
                </Mono>
                <a
                  href={`mailto:${inv.email}`}
                  className="text-text underline underline-offset-2 font-mono text-[12.5px]"
                >
                  {inv.email}
                </a>
              </div>

              <div className="flex flex-col gap-1.5">
                <Mono muted size={10}>
                  PROJECT
                </Mono>
                <p
                  className="m-0 text-[13.5px] leading-[1.55] whitespace-pre-wrap"
                >
                  {inv.project}
                </p>
              </div>

              {inv.ip && (
                <div className="flex gap-3 text-[11.5px] text-text-muted font-mono pt-2 border-t border-hair">
                  <span>{inv.ip}</span>
                  {inv.userAgent && (
                    <span className="truncate" title={inv.userAgent}>
                      {inv.userAgent}
                    </span>
                  )}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function formatDate(iso: string): string {
  try {
    const d = new Date(iso);
    return (
      d.toISOString().slice(0, 10) +
      " · " +
      d.toISOString().slice(11, 16) +
      " UTC"
    );
  } catch {
    return iso;
  }
}
