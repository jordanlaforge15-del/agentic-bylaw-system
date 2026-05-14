// /admin/analytics — tier distribution + upgrade-funnel dashboard.
// Server-rendered: aggregate queries against advisor_case_credit and
// advisor_case_event are cheap at our volume.

import { redirect } from "next/navigation";
import { requireAdmin } from "@/lib/admin-auth";
import { ADVISOR_API_URL } from "@/lib/api";
import { buildAdvisorAuthHeaders } from "@/lib/advisor-auth";
import { Mono } from "@/components/mono";

export const dynamic = "force-dynamic";


type TierDistributionRow = {
  tier: string;
  source: string;
  state: string;
  count: number;
};

type TierDistributionResponse = {
  rows: TierDistributionRow[];
};

type UpgradeFunnelRow = {
  event_type: string;
  count: number;
};

type UpgradeFunnelResponse = {
  rows: UpgradeFunnelRow[];
};


async function fetchAdmin<T>(path: string): Promise<T | null> {
  const headers = await buildAdvisorAuthHeaders();
  if (headers === null) return null;
  try {
    const r = await fetch(`${ADVISOR_API_URL}${path}`, {
      cache: "no-store",
      headers: { Accept: "application/json", ...headers },
    });
    if (!r.ok) return null;
    return (await r.json()) as T;
  } catch {
    return null;
  }
}


export default async function AdminAnalyticsPage() {
  const admin = await requireAdmin();
  if (!admin) {
    redirect("/sign-in");
  }
  const [dist, funnel] = await Promise.all([
    fetchAdmin<TierDistributionResponse>(
      "/v1/admin/analytics/tier-distribution",
    ),
    fetchAdmin<UpgradeFunnelResponse>(
      "/v1/admin/analytics/upgrade-funnel",
    ),
  ]);

  return (
    <div
      className="min-h-screen bg-surface text-text px-8 py-12 mx-auto"
      style={{ maxWidth: 1100 }}
    >
      <header className="flex flex-col gap-3 pb-6 mb-8 border-b border-hair">
        <Mono muted size={11}>
          ADMIN · ANALYTICS · {admin.email}
        </Mono>
        <h1
          className="font-sans font-extrabold m-0"
          style={{ fontSize: 44, letterSpacing: "-0.035em", lineHeight: 1 }}
        >
          Analytics
        </h1>
      </header>

      <section className="mb-12">
        <h2 className="font-sans font-extrabold mb-3 text-[20px]" style={{ letterSpacing: "-0.03em" }}>
          Tier distribution
        </h2>
        {dist === null ? (
          <ServiceUnavailable />
        ) : dist.rows.length === 0 ? (
          <Empty msg="No credit rows yet." />
        ) : (
          <DistributionTable rows={dist.rows} />
        )}
      </section>

      <section>
        <h2 className="font-sans font-extrabold mb-3 text-[20px]" style={{ letterSpacing: "-0.03em" }}>
          Upgrade funnel
        </h2>
        <p className="text-text-muted text-[12.5px] mb-3 max-w-[600px]">
          Counts of classifier recommendations vs. agent-fired upgrade
          offers vs. user accepts/declines. Conversion = accepts /
          (offers + classifier recommendations).
        </p>
        {funnel === null ? (
          <ServiceUnavailable />
        ) : funnel.rows.length === 0 ? (
          <Empty msg="No upgrade-related events yet." />
        ) : (
          <FunnelTable rows={funnel.rows} />
        )}
      </section>
    </div>
  );
}


function ServiceUnavailable() {
  return (
    <div className="bg-surface-alt border border-hair p-5 text-text-muted text-[13px]">
      Couldn&apos;t reach the analytics endpoint. Check that
      ``ADVISOR_ADMIN_API_ENABLED=true`` is set on the backend.
    </div>
  );
}


function Empty({ msg }: { msg: string }) {
  return (
    <div className="bg-surface-alt border border-hair p-5 text-text-muted text-[13px]">
      {msg}
    </div>
  );
}


function DistributionTable({ rows }: { rows: TierDistributionRow[] }) {
  // Group rows by (tier, source) for the row dimension; columns are
  // states (available / reserved / consumed / others).
  const states = Array.from(new Set(rows.map((r) => r.state))).sort();
  const groups = new Map<string, Map<string, number>>();
  for (const r of rows) {
    const key = `${r.tier}|${r.source}`;
    if (!groups.has(key)) groups.set(key, new Map());
    groups.get(key)!.set(r.state, (groups.get(key)!.get(r.state) ?? 0) + r.count);
  }
  return (
    <div className="border border-hair overflow-x-auto">
      <table className="w-full text-[13px] border-collapse">
        <thead>
          <tr className="bg-surface-alt text-left">
            <th className="px-4 py-2.5 font-mono text-[11px] uppercase text-text-muted">
              Tier · source
            </th>
            {states.map((s) => (
              <th
                key={s}
                className="px-4 py-2.5 font-mono text-[11px] uppercase text-text-muted text-right"
              >
                {s}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {[...groups.entries()].map(([key, byState]) => {
            const [tier, source] = key.split("|");
            return (
              <tr key={key} className="border-t border-hair">
                <td className="px-4 py-2.5">
                  <span className="capitalize">{tier}</span>{" "}
                  <span className="text-text-muted">· {source}</span>
                </td>
                {states.map((s) => (
                  <td key={s} className="px-4 py-2.5 text-right font-mono">
                    {byState.get(s) ?? 0}
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}


function FunnelTable({ rows }: { rows: UpgradeFunnelRow[] }) {
  return (
    <div className="border border-hair">
      <table className="w-full text-[13px] border-collapse">
        <thead>
          <tr className="bg-surface-alt text-left">
            <th className="px-4 py-2.5 font-mono text-[11px] uppercase text-text-muted">
              Event
            </th>
            <th className="px-4 py-2.5 font-mono text-[11px] uppercase text-text-muted text-right">
              Count
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.event_type} className="border-t border-hair">
              <td className="px-4 py-2.5 capitalize">
                {r.event_type.replace(/_/g, " ")}
              </td>
              <td className="px-4 py-2.5 text-right font-mono">{r.count}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
