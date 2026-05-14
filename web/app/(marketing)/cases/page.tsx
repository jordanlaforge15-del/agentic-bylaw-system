// /cases — case browser. Lives under the marketing chrome (same as
// /billing) because it's an account-management surface rather than the
// chat product itself.

import Link from "next/link";
import { ADVISOR_API_URL } from "@/lib/api";
import {
  CaseListResponse,
  TIER_DISPLAY,
} from "@/lib/cases";
import { buildAdvisorAuthHeaders } from "@/lib/advisor-auth";
import { Btn } from "@/components/btn";
import { Mono } from "@/components/mono";

export const dynamic = "force-dynamic";


async function fetchAuthed<T>(path: string): Promise<T | { _unauthorized: true } | null> {
  const headers = await buildAdvisorAuthHeaders();
  if (headers === null) return { _unauthorized: true };
  try {
    const r = await fetch(`${ADVISOR_API_URL}${path}`, {
      cache: "no-store",
      headers: { Accept: "application/json", ...headers },
    });
    if (r.status === 401) return { _unauthorized: true };
    if (!r.ok) return null;
    return (await r.json()) as T;
  } catch {
    return null;
  }
}


export default async function CasesPage() {
  const cases = await fetchAuthed<CaseListResponse>("/v1/cases");
  const unauthorized = cases && "_unauthorized" in cases;
  const rows = (cases as CaseListResponse | null)?.cases ?? [];

  return (
    <div
      className="px-5 sm:px-8 py-10 sm:py-12 lg:py-14 mx-auto max-w-[1100px]"
      style={{ minHeight: "calc(100vh - 280px)" }}
    >
      <header className="flex flex-col gap-3 sm:gap-3.5 pb-6 sm:pb-7 mb-7 sm:mb-9 border-b border-hair">
        <Mono muted size={11}>
          ACCOUNT · CASES
        </Mono>
        <div className="flex items-baseline justify-between gap-3 flex-wrap">
          <h1
            className="font-sans font-extrabold m-0 text-[28px] sm:text-[36px] lg:text-[42px] leading-[1]"
            style={{ letterSpacing: "-0.04em" }}
          >
            My cases
          </h1>
          <Link href="/cases/new">
            <Btn variant="accent" size="sm">
              Open a case
            </Btn>
          </Link>
        </div>
      </header>

      {unauthorized ? (
        <div className="bg-surface-alt border border-hair p-8">
          <div className="font-semibold mb-2">Sign in to view your cases</div>
          <div className="text-text-muted text-[13.5px] mb-4">
            Your case list and credit balance live behind your account.
          </div>
          <Link href="/login?next=/cases">
            <Btn variant="primary" size="sm">
              Sign in
            </Btn>
          </Link>
        </div>
      ) : rows.length === 0 ? (
        <div className="bg-surface-alt border border-hair p-8">
          <div className="font-semibold mb-2">No cases yet</div>
          <div className="text-text-muted text-[13.5px] mb-4">
            Open a case for a property, project, or development
            application. Each case is one credit.
          </div>
          <Link href="/cases/new">
            <Btn variant="primary" size="sm">
              Open your first case
            </Btn>
          </Link>
        </div>
      ) : (
        <div className="border border-hair">
          <table className="w-full text-[13px] border-collapse">
            <thead>
              <tr className="bg-surface-alt text-left">
                <th className="px-4 py-2.5 font-mono text-[11px] uppercase text-text-muted">Anchor</th>
                <th className="px-4 py-2.5 font-mono text-[11px] uppercase text-text-muted">Kind</th>
                <th className="px-4 py-2.5 font-mono text-[11px] uppercase text-text-muted">Tier</th>
                <th className="px-4 py-2.5 font-mono text-[11px] uppercase text-text-muted">Status</th>
                <th className="px-4 py-2.5 font-mono text-[11px] uppercase text-text-muted text-right">Tokens used</th>
                <th className="px-4 py-2.5 font-mono text-[11px] uppercase text-text-muted text-right">Last activity</th>
                <th className="px-4 py-2.5"></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((c) => (
                <tr key={c.id} className="border-t border-hair">
                  <td className="px-4 py-2.5 truncate max-w-[260px]">
                    {c.anchor_label}
                  </td>
                  <td className="px-4 py-2.5 capitalize text-text-muted">
                    {c.anchor_kind.replace("_", " ")}
                  </td>
                  <td className="px-4 py-2.5">
                    {c.current_tier ? TIER_DISPLAY[c.current_tier] : "—"}
                  </td>
                  <td className="px-4 py-2.5 capitalize">{c.status}</td>
                  <td className="px-4 py-2.5 text-right font-mono text-text-muted">
                    {c.tokens_consumed.toLocaleString()}
                  </td>
                  <td className="px-4 py-2.5 text-right text-text-muted">
                    {new Date(c.last_activity_at).toLocaleDateString("en-CA")}
                  </td>
                  <td className="px-4 py-2.5 text-right">
                    <Link
                      href={`/app?case_id=${c.id}`}
                      className="underline text-[12.5px]"
                    >
                      Open
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
