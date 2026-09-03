// ABS-501: single place that answers "which Postgres do the e2e seeds
// write to?".
//
// Every globalSetup/spec used to inline this:
//
//   const pgPort = process.env.PG_PORT || "5433";
//   const databaseUrl =
//     process.env.DATABASE_URL ||
//     `postgresql+psycopg://layer1:layer1@localhost:${pgPort}/layer1_test`;
//
// which prefers an *inherited* DATABASE_URL over PG_PORT. scripts/e2e-up.sh
// exports DATABASE_URL for the stack it just booted, and the Night Manager's
// agent runner exports one pinned to the run's assigned port — both outlive
// the stack they described. A shell carrying a stale value then seeds a dead
// port while FastAPI queries the live one, and the suite reports a green
// branch as broken (ABS-492 burned ~$17 of agent time chasing exactly that).
//
// PG_PORT is set by whoever owns the stack that is up *now*, so it wins on
// disagreement — loudly, never silently. Mirrors
// `layer1.seed_guard.reconcile_database_url`; tests/test_abs501_* pins the
// two implementations to the same rule.

/** Default host port of the dedicated e2e Postgres instance (ABS-428). */
export const E2E_PG_PORT_DEFAULT = "5433";

export interface ResolvedDatabaseUrl {
  /** URL the caller should hand to seed scripts. */
  url: string;
  /** Set only when an inherited DATABASE_URL was overridden by PG_PORT. */
  warning?: string;
}

/**
 * Resolve the e2e DATABASE_URL from `env` (defaults to `process.env`)
 * without touching the process environment. Pure — exported separately
 * from {@link resolveDatabaseUrl} so tests can drive it with fabricated
 * environments.
 */
export function reconcileDatabaseUrl(
  env: NodeJS.ProcessEnv = process.env,
): ResolvedDatabaseUrl {
  const pgPort = env.PG_PORT || E2E_PG_PORT_DEFAULT;
  const derived = `postgresql+psycopg://layer1:layer1@localhost:${pgPort}/layer1_test`;
  const inherited = env.DATABASE_URL;

  if (!inherited) return { url: derived };
  // No explicit PG_PORT means nothing to disagree with: the caller's
  // DATABASE_URL is the only signal available.
  if (!env.PG_PORT) return { url: inherited };
  if (inherited.startsWith("sqlite")) return { url: inherited };

  const inheritedPort = portOf(inherited);
  if (inheritedPort === null || inheritedPort === env.PG_PORT) {
    return { url: inherited };
  }

  const rewritten = inherited.replace(
    `:${inheritedPort}/`,
    `:${env.PG_PORT}/`,
  );
  return {
    url: rewritten,
    warning:
      `DATABASE_URL/PG_PORT conflict: inherited DATABASE_URL targets port ` +
      `${inheritedPort} but PG_PORT=${env.PG_PORT}. PG_PORT wins — using ` +
      `${rewritten}. A stale DATABASE_URL from a torn-down stack is the ` +
      `usual cause; \`unset DATABASE_URL\` to silence this. [ABS-501]`,
  };
}

/**
 * {@link reconcileDatabaseUrl}, with the conflict printed to the console.
 * This is what globalSetup and the seeding specs call.
 */
export function resolveDatabaseUrl(
  env: NodeJS.ProcessEnv = process.env,
): string {
  const { url, warning } = reconcileDatabaseUrl(env);
  if (warning) console.warn(`e2e env preflight: ${warning}`);
  return url;
}

/** Host port of a `scheme://user:pw@host:port/db` URL, or null if absent. */
function portOf(url: string): string | null {
  const match = /^[a-z+]+:\/\/[^/@]*@?[^/:]+:(\d+)\//i.exec(url);
  return match ? match[1] : null;
}
