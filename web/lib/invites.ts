// Shared data-access layer for the invite_request table.
//
// Used by:
//   - app/api/invite/route.ts          (public POST to request access)
//   - app/api/admin/invites/...        (admin approve / reject / sweep)
//   - app/admin/invites/page.tsx       (admin list view)
//
// We keep all SQL in one place so a schema change touches one file
// rather than four route handlers. Functions throw on unexpected
// failures (Postgres error, missing row when expected); routes catch
// and translate to HTTP status.

import { query } from "@/lib/db";

export type InviteStatus = "pending" | "approved" | "rejected" | "expired";

export type InviteRequestRow = {
  id: string;
  email: string;
  name: string;
  role: string | null;
  project: string | null;
  status: InviteStatus;
  created_at: string;
  decided_at: string | null;
  decided_by: string | null;
  expires_at: string | null;
  redeemed_at: string | null;
  clerk_allowlist_id: string | null;
  granted_query_limit: number;
  granted_monthly_input_tokens: number;
  granted_monthly_output_tokens: number;
  granted_rpm: number;
  ip: string | null;
  user_agent: string | null;
  notes: string | null;
};

// 14 days — see the migration comment for why this anchor is short.
// If you raise this, also raise the default in the DB column
// server_default for consistency.
export const INVITE_EXPIRY_DAYS = 14;

// Match Postgres bigint -> number when the value fits. node-postgres
// returns bigints as strings by default to avoid silent overflow;
// our token caps fit comfortably in JS's safe integer range so a
// cast is fine.
function bigintToNumber(v: unknown): number {
  if (typeof v === "number") return v;
  if (typeof v === "string") {
    const n = Number(v);
    return Number.isFinite(n) ? n : 0;
  }
  return 0;
}

function normalize(row: Record<string, unknown>): InviteRequestRow {
  return {
    id: String(row.id),
    email: String(row.email),
    name: String(row.name),
    role: (row.role as string | null) ?? null,
    project: (row.project as string | null) ?? null,
    status: row.status as InviteStatus,
    created_at: (row.created_at as Date).toISOString(),
    decided_at: row.decided_at
      ? (row.decided_at as Date).toISOString()
      : null,
    decided_by: (row.decided_by as string | null) ?? null,
    expires_at: row.expires_at
      ? (row.expires_at as Date).toISOString()
      : null,
    redeemed_at: row.redeemed_at
      ? (row.redeemed_at as Date).toISOString()
      : null,
    clerk_allowlist_id: (row.clerk_allowlist_id as string | null) ?? null,
    granted_query_limit: bigintToNumber(row.granted_query_limit),
    granted_monthly_input_tokens: bigintToNumber(
      row.granted_monthly_input_tokens,
    ),
    granted_monthly_output_tokens: bigintToNumber(
      row.granted_monthly_output_tokens,
    ),
    granted_rpm: bigintToNumber(row.granted_rpm),
    ip: (row.ip as string | null) ?? null,
    user_agent: (row.user_agent as string | null) ?? null,
    notes: (row.notes as string | null) ?? null,
  };
}

export async function listInvites(): Promise<InviteRequestRow[]> {
  // Order: pending first (where the admin's eyes go), then by
  // recency. CASE preserves a stable order across status groups.
  const { rows } = await query<Record<string, unknown>>(
    `SELECT *
       FROM invite_request
      ORDER BY CASE status
                 WHEN 'pending' THEN 0
                 WHEN 'approved' THEN 1
                 WHEN 'rejected' THEN 2
                 WHEN 'expired' THEN 3
                 ELSE 4 END,
               created_at DESC`,
  );
  return rows.map(normalize);
}

export async function getInvite(id: string): Promise<InviteRequestRow | null> {
  const { rows } = await query<Record<string, unknown>>(
    "SELECT * FROM invite_request WHERE id = $1",
    [id],
  );
  if (rows.length === 0) return null;
  return normalize(rows[0]);
}

export async function getInviteByEmail(
  email: string,
): Promise<InviteRequestRow | null> {
  const { rows } = await query<Record<string, unknown>>(
    "SELECT * FROM invite_request WHERE LOWER(email) = LOWER($1)",
    [email],
  );
  if (rows.length === 0) return null;
  return normalize(rows[0]);
}

// New random-suffix id. Collision probability is tiny at our volume,
// and the email UNIQUE constraint prevents the failure mode that
// matters (someone submitting twice).
export function generateInviteId(): string {
  const n = Math.floor(1000 + Math.random() * 9000);
  return `ABS-${n}`;
}

export type CreateInviteInput = {
  email: string;
  name: string;
  role?: string;
  project?: string;
  ip?: string | null;
  userAgent?: string | null;
};

// Insert a new pending invite. If the email already has a request,
// returns the existing row instead of inserting a duplicate — this
// preserves the original submission timestamp and avoids spamming the
// admin queue.
export async function createInvite(
  input: CreateInviteInput,
): Promise<{ row: InviteRequestRow; reused: boolean }> {
  const existing = await getInviteByEmail(input.email);
  if (existing) {
    return { row: existing, reused: true };
  }
  const id = generateInviteId();
  const { rows } = await query<Record<string, unknown>>(
    `INSERT INTO invite_request
       (id, email, name, role, project, status, created_at, ip, user_agent)
     VALUES ($1, $2, $3, $4, $5, 'pending', now(), $6, $7)
     RETURNING *`,
    [
      id,
      input.email,
      input.name,
      input.role ?? null,
      input.project ?? null,
      input.ip ?? null,
      input.userAgent ?? null,
    ],
  );
  return { row: normalize(rows[0]), reused: false };
}

export type ApproveInviteInput = {
  id: string;
  decidedBy: string;
  clerkAllowlistId: string;
  // Optional per-invite cap overrides; if absent, the DB defaults
  // (set by the migration) stay in place.
  queryLimit?: number;
  monthlyInputTokens?: number;
  monthlyOutputTokens?: number;
  rpm?: number;
};

// Flip a pending invite to approved + set expires_at = now + 14 days.
// Caller is responsible for having already added the email to Clerk's
// allowlist (and is passing in the resulting alid).
export async function approveInvite(
  input: ApproveInviteInput,
): Promise<InviteRequestRow | null> {
  const { rows } = await query<Record<string, unknown>>(
    `UPDATE invite_request
        SET status = 'approved',
            decided_at = now(),
            decided_by = $2,
            clerk_allowlist_id = $3,
            expires_at = now() + INTERVAL '${INVITE_EXPIRY_DAYS} days',
            granted_query_limit = COALESCE($4, granted_query_limit),
            granted_monthly_input_tokens = COALESCE($5, granted_monthly_input_tokens),
            granted_monthly_output_tokens = COALESCE($6, granted_monthly_output_tokens),
            granted_rpm = COALESCE($7, granted_rpm)
      WHERE id = $1
        AND status = 'pending'
      RETURNING *`,
    [
      input.id,
      input.decidedBy,
      input.clerkAllowlistId,
      input.queryLimit ?? null,
      input.monthlyInputTokens ?? null,
      input.monthlyOutputTokens ?? null,
      input.rpm ?? null,
    ],
  );
  if (rows.length === 0) return null;
  return normalize(rows[0]);
}

export async function rejectInvite(
  id: string,
  decidedBy: string,
): Promise<InviteRequestRow | null> {
  const { rows } = await query<Record<string, unknown>>(
    `UPDATE invite_request
        SET status = 'rejected',
            decided_at = now(),
            decided_by = $2
      WHERE id = $1
        AND status = 'pending'
      RETURNING *`,
    [id, decidedBy],
  );
  if (rows.length === 0) return null;
  return normalize(rows[0]);
}

// Find approved invites past their expiry that haven't been
// redeemed. Returns rows the sweeper should remove from Clerk's
// allowlist and mark expired.
export async function findExpiredApprovedInvites(): Promise<InviteRequestRow[]> {
  const { rows } = await query<Record<string, unknown>>(
    `SELECT *
       FROM invite_request
      WHERE status = 'approved'
        AND expires_at IS NOT NULL
        AND expires_at < now()
        AND redeemed_at IS NULL`,
  );
  return rows.map(normalize);
}

export async function markInviteExpired(id: string): Promise<void> {
  await query(
    `UPDATE invite_request SET status = 'expired' WHERE id = $1`,
    [id],
  );
}
