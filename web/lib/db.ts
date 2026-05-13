// Postgres connection pool shared across web API routes.
//
// Reads DATABASE_URL from env (set in /srv/bylaw/.env on prod, where
// it's the same Postgres instance the advisor + layer1 use — schemas
// are logically separated by table-name prefix).
//
// Module-level pool is intentional: Next.js may invoke route handlers
// many times per process, and creating a new connection per request
// would burn the local port range fast. The Pool also handles
// disconnect+reconnect on transient Postgres restarts.

import { Pool } from "pg";

declare global {
  // eslint-disable-next-line no-var
  var __pgPool: Pool | undefined;
}

// In Next.js dev with HMR, module re-execution would leak pools.
// Stash on globalThis so a single pool survives reload cycles.
export const pool: Pool =
  global.__pgPool ??
  new Pool({
    connectionString: process.env.DATABASE_URL,
    // 10 is plenty for the admin + invite request workload; raising
    // this hits Postgres's max_connections (typically 100) faster
    // than it relieves any real bottleneck. Increase only if we see
    // pool exhaustion in logs.
    max: 10,
    idleTimeoutMillis: 30_000,
  });

if (process.env.NODE_ENV !== "production") {
  global.__pgPool = pool;
}

// Tiny tagged-template-style helper for readability at call sites:
//
//   const { rows } = await query<{id: string}>(
//     "SELECT id FROM invite_request WHERE email = $1",
//     [email],
//   );
//
// Returns the underlying QueryResult so callers can destructure
// rowCount as well as rows.
export async function query<T extends Record<string, unknown> = Record<string, unknown>>(
  text: string,
  params?: unknown[],
): Promise<{ rows: T[]; rowCount: number }> {
  const result = await pool.query(text, params);
  return { rows: result.rows as T[], rowCount: result.rowCount ?? 0 };
}
