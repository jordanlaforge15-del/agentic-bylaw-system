// /admin/cases — case browser with status + tier filters. Reuses
// the existing requireAdmin / Clerk allowlist gate.

import { redirect } from "next/navigation";
import Link from "next/link";
import { requireAdmin } from "@/lib/admin-auth";
import { ADVISOR_API_URL } from "@/lib/api";
import { buildAdvisorAuthHeaders } from "@/lib/advisor-auth";
import { CaseListResponse, TIER_DISPLAY } from "@/lib/cases";
import { Mono } from "@/components/mono";

export const dynamic = "force-dynamic";


type Search = { status?: string; tier?: string };


async function fetchCases(search: Search): Promise<CaseListResponse | null> {
  const headers = await buildAdvisorAuthHeaders();
  if (headers === null) return null;
  const url = new URL("/v1/admin/cases", ADVISOR_API_URL);
  if (search.status) url.searchParams.set("status", search.status);
  if (search.tier) url.searchParams.set("tier", search.tier);
  try {
    const r = await fetch(url.toString(), {
      cache: "no-store",
      headers: { Accept: "application/json", ...headers },
    });
    if (!r.ok) return null;
    return (await r.json()) as CaseListResponse;
  } catch {
    return null;
  }
}


export default async function AdminCasesPage({
  searchParams,
}: {
  searchParams: Promise<{ status?: string; tier?: string }>;
}) {
  const admin = await requireAdmin();
  if (!admin) {
    redirect("/sign-in");
  }
  const params = await searchParams;
  const data = await fetchCases({ status: params.status, tier: params.tier });
  const cases = data?.cases ?? [];

  return (
    <div
      className="min-h-screen bg-surface text-text px-8 py-12 mx-auto"
      style={{ maxWidth: 1200 }}
    >
      <header className="flex flex-col gap-3 pb-6 mb-8 border-b border-hair">
        <Mono muted size={11}>
          ADMIN · CASES · {admin.email}
        </Mono>
        <h1
          className="font-sans font-extrabold m-0"
          style={{ fontSize: 44, letterSpacing: "-0.035em", lineHeight: 1 }}
        >
          Cases
        </h1>
        <p className="text-text-muted text-[13.5px] max-w-[640px]">
          Read-only audit view of every case across the platform.
          Filter by status or tier via the URL params.
        </p>
        <div className="flex gap-2 flex-wrap mt-2">
          <FilterChip label="All" href="/admin/cases" active={!params.status && !params.tier} />
          <FilterChip label="Open" href="/admin/cases?status=open" active={params.status === "open"} />
          <FilterChip label="Closed" href="/admin/cases?status=closed" active={params.status === "closed"} />
          <FilterChip label="Quick" href="/admin/cases?tier=quick" active={params.tier === "quick"} />
          <FilterChip label="Standard" href="/admin/cases?tier=standard" active={params.tier === "standard"} />
          <FilterChip label="Complex" href="/admin/cases?tier=complex" active={params.tier === "complex"} />
        </div>
      </header>

      {cases.length === 0 ? (
        <div className="bg-surface-alt border border-hair p-8 text-text-muted text-[13px]">
          No cases match this filter.
        </div>
      ) : (
        <div className="border border-hair">
          <table className="w-full text-[13px] border-collapse">
            <thead>
              <tr className="bg-surface-alt text-left">
                <th className="px-4 py-2.5 font-mono text-[11px] uppercase text-text-muted">ID</th>
                <th className="px-4 py-2.5 font-mono text-[11px] uppercase text-text-muted">User</th>
                <th className="px-4 py-2.5 font-mono text-[11px] uppercase text-text-muted">Anchor</th>
                <th className="px-4 py-2.5 font-mono text-[11px] uppercase text-text-muted">Tier</th>
                <th className="px-4 py-2.5 font-mono text-[11px] uppercase text-text-muted">Status</th>
                <th className="px-4 py-2.5 font-mono text-[11px] uppercase text-text-muted text-right">Tokens</th>
                <th className="px-4 py-2.5 font-mono text-[11px] uppercase text-text-muted text-right">Last activity</th>
              </tr>
            </thead>
            <tbody>
              {cases.map((c) => (
                <tr key={c.id} className="border-t border-hair">
                  <td className="px-4 py-2.5 font-mono">{c.id}</td>
                  <td className="px-4 py-2.5 text-text-muted">{c.user_id}</td>
                  <td className="px-4 py-2.5 truncate max-w-[260px]">{c.anchor_label}</td>
                  <td className="px-4 py-2.5">
                    {c.current_tier ? TIER_DISPLAY[c.current_tier] : "—"}
                  </td>
                  <td className="px-4 py-2.5 capitalize">{c.status}</td>
                  <td className="px-4 py-2.5 text-right font-mono">
                    {c.tokens_consumed.toLocaleString()}
                  </td>
                  <td className="px-4 py-2.5 text-right text-text-muted">
                    {new Date(c.last_activity_at).toLocaleString("en-CA")}
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


function FilterChip({
  label,
  href,
  active,
}: {
  label: string;
  href: string;
  active: boolean;
}) {
  const tone = active
    ? "bg-text text-surface border-text"
    : "bg-transparent text-text-muted border-hair";
  return (
    <Link
      href={href}
      className={`${tone} border px-3 py-1 text-[12px] font-mono uppercase`}
    >
      {label}
    </Link>
  );
}
