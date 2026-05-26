// /admin/analytics — aggregate usage analytics dashboard.
// Server-rendered: aggregate queries against the advisor tables are
// cheap at our volume. No charting library — CSS-based visualizations.

import { redirect } from "next/navigation";
import { requireAdmin } from "@/lib/admin-auth";
import { ADVISOR_API_URL } from "@/lib/api";
import { buildAdvisorAuthHeaders } from "@/lib/advisor-auth";
import { Mono } from "@/components/mono";

export const dynamic = "force-dynamic";


// -- Types -------------------------------------------------------------------

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

type ActiveUsersDayRow = { date: string; count: number };
type ActiveUsersWeekRow = { week: string; count: number };
type ActiveUsersResponse = {
  daily: ActiveUsersDayRow[];
  weekly: ActiveUsersWeekRow[];
};

type EngagementWeekRow = { week: string; value: number };
type EngagementResponse = {
  sessions_per_user: EngagementWeekRow[];
  messages_per_session: EngagementWeekRow[];
};

type RetentionCohortRow = {
  cohort_week: string;
  signup_count: number;
  retention_pcts: number[];
};
type RetentionResponse = {
  cohorts: RetentionCohortRow[];
  week_labels: string[];
};

type CreditTrendRow = { date: string; tier: string; count: number };
type CreditTrendsResponse = { daily: CreditTrendRow[] };

type FunnelStage = { name: string; count: number };
type FunnelResponse = { stages: FunnelStage[] };


// -- Data fetching -----------------------------------------------------------

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


// -- Page --------------------------------------------------------------------

export default async function AdminAnalyticsPage() {
  const admin = await requireAdmin();
  if (!admin) {
    redirect("/sign-in");
  }

  const [dist, funnel, activeUsers, engagement, retention, creditTrends, funnelData] =
    await Promise.all([
      fetchAdmin<TierDistributionResponse>("/v1/admin/analytics/tier-distribution"),
      fetchAdmin<UpgradeFunnelResponse>("/v1/admin/analytics/upgrade-funnel"),
      fetchAdmin<ActiveUsersResponse>("/v1/admin/analytics/active-users"),
      fetchAdmin<EngagementResponse>("/v1/admin/analytics/engagement"),
      fetchAdmin<RetentionResponse>("/v1/admin/analytics/retention"),
      fetchAdmin<CreditTrendsResponse>("/v1/admin/analytics/credit-trends"),
      fetchAdmin<FunnelResponse>("/v1/admin/analytics/funnel"),
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

      {/* Funnel visualization */}
      <Section title="User funnel">
        <p className="text-text-muted text-[12.5px] mb-3 max-w-[600px]">
          Conversion through the product lifecycle: signup, terms acceptance,
          first question asked, repeat usage, and purchase.
        </p>
        {funnelData === null ? (
          <ServiceUnavailable />
        ) : funnelData.stages.length === 0 ? (
          <Empty msg="No funnel data yet." />
        ) : (
          <FunnelViz stages={funnelData.stages} />
        )}
      </Section>

      {/* Active users */}
      <Section title="Active users">
        {activeUsers === null ? (
          <ServiceUnavailable />
        ) : activeUsers.daily.length === 0 && activeUsers.weekly.length === 0 ? (
          <Empty msg="No session activity yet." />
        ) : (
          <div className="flex flex-col gap-8">
            {activeUsers.weekly.length > 0 && (
              <div>
                <Mono muted size={10} className="mb-2 block">Weekly active users</Mono>
                <BarChart
                  data={activeUsers.weekly.map((r) => ({ label: r.week, value: r.count }))}
                />
              </div>
            )}
            {activeUsers.daily.length > 0 && (
              <div>
                <Mono muted size={10} className="mb-2 block">Daily active users (last 30 days)</Mono>
                <BarChart
                  data={activeUsers.daily.map((r) => ({ label: r.date, value: r.count }))}
                  compact
                />
              </div>
            )}
          </div>
        )}
      </Section>

      {/* Engagement */}
      <Section title="Engagement">
        {engagement === null ? (
          <ServiceUnavailable />
        ) : engagement.sessions_per_user.length === 0 ? (
          <Empty msg="No engagement data yet." />
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
            <div>
              <Mono muted size={10} className="mb-2 block">Sessions per user (weekly)</Mono>
              <BarChart
                data={engagement.sessions_per_user.map((r) => ({
                  label: r.week,
                  value: r.value,
                }))}
                decimals={1}
              />
            </div>
            <div>
              <Mono muted size={10} className="mb-2 block">Messages per session (weekly)</Mono>
              <BarChart
                data={engagement.messages_per_session.map((r) => ({
                  label: r.week,
                  value: r.value,
                }))}
                decimals={1}
              />
            </div>
          </div>
        )}
      </Section>

      {/* Retention cohorts */}
      <Section title="Retention cohorts">
        <p className="text-text-muted text-[12.5px] mb-3 max-w-[600px]">
          Percentage of each signup cohort that returned in subsequent weeks.
          Darker cells indicate higher retention.
        </p>
        {retention === null ? (
          <ServiceUnavailable />
        ) : retention.cohorts.length === 0 ? (
          <Empty msg="No retention data yet." />
        ) : (
          <RetentionTable
            cohorts={retention.cohorts}
            weekLabels={retention.week_labels}
          />
        )}
      </Section>

      {/* Credit consumption trends */}
      <Section title="Credit consumption trends">
        {creditTrends === null ? (
          <ServiceUnavailable />
        ) : creditTrends.daily.length === 0 ? (
          <Empty msg="No credit consumption yet." />
        ) : (
          <CreditTrendsChart data={creditTrends.daily} />
        )}
      </Section>

      {/* Tier distribution */}
      <Section title="Tier distribution">
        {dist === null ? (
          <ServiceUnavailable />
        ) : dist.rows.length === 0 ? (
          <Empty msg="No credit rows yet." />
        ) : (
          <DistributionTable rows={dist.rows} />
        )}
      </Section>

      {/* Upgrade funnel */}
      <Section title="Upgrade funnel">
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
          <UpgradeFunnelTable rows={funnel.rows} />
        )}
      </Section>
    </div>
  );
}


// -- Shared components -------------------------------------------------------

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mb-12" data-testid={`analytics-${title.toLowerCase().replace(/\s+/g, "-")}`}>
      <h2
        className="font-sans font-extrabold mb-3 text-[20px]"
        style={{ letterSpacing: "-0.03em" }}
      >
        {title}
      </h2>
      {children}
    </section>
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


// -- Bar chart (CSS-based) ---------------------------------------------------

function BarChart({
  data,
  compact,
  decimals = 0,
}: {
  data: { label: string; value: number }[];
  compact?: boolean;
  decimals?: number;
}) {
  const max = Math.max(...data.map((d) => d.value), 1);
  return (
    <div className="flex flex-col gap-1">
      {data.map((d) => {
        const pct = (d.value / max) * 100;
        return (
          <div key={d.label} className="flex items-center gap-2 text-[12px]">
            <span
              className="text-text-muted font-mono shrink-0 text-right"
              style={{ width: compact ? 70 : 80 }}
            >
              {compact ? d.label.slice(5) : d.label}
            </span>
            <div className="flex-1 h-4 bg-surface-alt rounded-sm overflow-hidden">
              <div
                className="h-full bg-accent-ink rounded-sm"
                style={{ width: `${pct}%`, minWidth: d.value > 0 ? 2 : 0 }}
              />
            </div>
            <span className="font-mono text-[11px] text-text-muted shrink-0 w-10 text-right">
              {decimals > 0 ? d.value.toFixed(decimals) : d.value}
            </span>
          </div>
        );
      })}
    </div>
  );
}


// -- Funnel visualization ----------------------------------------------------

function FunnelViz({ stages }: { stages: FunnelStage[] }) {
  const max = Math.max(...stages.map((s) => s.count), 1);
  return (
    <div className="flex flex-col gap-2" data-testid="funnel-stages">
      {stages.map((stage, i) => {
        const pct = (stage.count / max) * 100;
        const conversionPct =
          i > 0 && stages[i - 1].count > 0
            ? ((stage.count / stages[i - 1].count) * 100).toFixed(1)
            : null;
        return (
          <div key={stage.name} className="flex items-center gap-3">
            <span className="text-[13px] font-medium w-32 shrink-0">
              {stage.name}
            </span>
            <div className="flex-1 h-7 bg-surface-alt rounded-sm overflow-hidden">
              <div
                className="h-full rounded-sm flex items-center px-2"
                style={{
                  width: `${Math.max(pct, 3)}%`,
                  backgroundColor: `hsl(${220 - i * 20}, 60%, ${55 + i * 5}%)`,
                }}
              >
                <span className="text-[11px] font-mono text-white font-medium">
                  {stage.count}
                </span>
              </div>
            </div>
            {conversionPct !== null && (
              <span className="text-[11px] font-mono text-text-muted shrink-0 w-14 text-right">
                {conversionPct}%
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}


// -- Retention table (heat-map style) ----------------------------------------

function RetentionTable({
  cohorts,
  weekLabels,
}: {
  cohorts: RetentionCohortRow[];
  weekLabels: string[];
}) {
  return (
    <div className="border border-hair overflow-x-auto">
      <table className="w-full text-[13px] border-collapse">
        <thead>
          <tr className="bg-surface-alt text-left">
            <th className="px-3 py-2.5 font-mono text-[11px] uppercase text-text-muted">
              Cohort
            </th>
            <th className="px-3 py-2.5 font-mono text-[11px] uppercase text-text-muted text-right">
              Users
            </th>
            {weekLabels.map((w) => (
              <th
                key={w}
                className="px-3 py-2.5 font-mono text-[11px] uppercase text-text-muted text-center"
              >
                {w}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {cohorts.map((c) => (
            <tr key={c.cohort_week} className="border-t border-hair">
              <td className="px-3 py-2.5 font-mono text-[12px]">
                {c.cohort_week}
              </td>
              <td className="px-3 py-2.5 text-right font-mono">
                {c.signup_count}
              </td>
              {c.retention_pcts.map((pct, i) => (
                <td
                  key={weekLabels[i]}
                  className="px-3 py-2.5 text-center font-mono text-[12px]"
                  style={{
                    backgroundColor: retentionColor(pct),
                    color: pct > 40 ? "white" : undefined,
                  }}
                >
                  {pct.toFixed(0)}%
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function retentionColor(pct: number): string {
  const clamped = Math.min(Math.max(pct, 0), 100);
  const alpha = clamped / 100;
  return `hsla(220, 60%, 50%, ${alpha * 0.8})`;
}


// -- Credit trends -----------------------------------------------------------

function CreditTrendsChart({ data }: { data: CreditTrendRow[] }) {
  const byDate = new Map<string, Map<string, number>>();
  const tiers = new Set<string>();
  for (const r of data) {
    tiers.add(r.tier);
    if (!byDate.has(r.date)) byDate.set(r.date, new Map());
    byDate.get(r.date)!.set(r.tier, r.count);
  }
  const sortedTiers = [...tiers].sort();
  const sortedDates = [...byDate.keys()].sort();
  const maxTotal = Math.max(
    ...sortedDates.map((d) => {
      let sum = 0;
      for (const t of sortedTiers) sum += byDate.get(d)?.get(t) ?? 0;
      return sum;
    }),
    1,
  );

  const tierColors: Record<string, string> = {
    quick: "hsl(200, 60%, 55%)",
    standard: "hsl(260, 50%, 55%)",
    complex: "hsl(330, 50%, 55%)",
  };

  return (
    <div>
      <div className="flex gap-4 mb-3">
        {sortedTiers.map((t) => (
          <div key={t} className="flex items-center gap-1.5 text-[11px]">
            <div
              className="w-3 h-3 rounded-sm"
              style={{ backgroundColor: tierColors[t] ?? "hsl(0, 0%, 60%)" }}
            />
            <span className="capitalize">{t}</span>
          </div>
        ))}
      </div>
      <div className="flex flex-col gap-1">
        {sortedDates.map((date) => {
          const tierMap = byDate.get(date)!;
          const total = sortedTiers.reduce(
            (s, t) => s + (tierMap.get(t) ?? 0),
            0,
          );
          return (
            <div key={date} className="flex items-center gap-2 text-[12px]">
              <span className="text-text-muted font-mono shrink-0 w-[70px] text-right">
                {date.slice(5)}
              </span>
              <div className="flex-1 h-4 bg-surface-alt rounded-sm overflow-hidden flex">
                {sortedTiers.map((t) => {
                  const count = tierMap.get(t) ?? 0;
                  if (count === 0) return null;
                  const pct = (count / maxTotal) * 100;
                  return (
                    <div
                      key={t}
                      className="h-full"
                      style={{
                        width: `${pct}%`,
                        backgroundColor: tierColors[t] ?? "hsl(0, 0%, 60%)",
                        minWidth: 2,
                      }}
                    />
                  );
                })}
              </div>
              <span className="font-mono text-[11px] text-text-muted shrink-0 w-8 text-right">
                {total}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}


// -- Tier distribution table -------------------------------------------------

function DistributionTable({ rows }: { rows: TierDistributionRow[] }) {
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


// -- Upgrade funnel table ----------------------------------------------------

function UpgradeFunnelTable({ rows }: { rows: UpgradeFunnelRow[] }) {
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
