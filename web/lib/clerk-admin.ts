// Thin wrapper over Clerk's Backend API for the admin flows.
// Only the operations we use today; expand as needed.
//
// Auth: uses CLERK_SECRET_KEY from env (already set on the server
// for backend JWT verification). All requests are HTTPS to
// api.clerk.com — never expose this key to the browser.

const CLERK_BACKEND_URL = "https://api.clerk.com";

function secretKey(): string {
  const k = process.env.CLERK_SECRET_KEY;
  if (!k || k.includes("replace")) {
    throw new Error("CLERK_SECRET_KEY is not configured");
  }
  return k;
}

async function clerkFetch(
  path: string,
  init: RequestInit = {},
): Promise<Response> {
  return fetch(`${CLERK_BACKEND_URL}${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${secretKey()}`,
      "Content-Type": "application/json",
      ...(init.headers || {}),
    },
  });
}

// Add an email to the Clerk allowlist. Returns the alid_… so we can
// reference it later (to delete on invite expiry).
//
// Idempotency note: Clerk's API returns 422 with code
// "duplicate_record" if the email is already on the allowlist. We
// treat that as success and fetch the existing entry's id.
export async function addToAllowlist(email: string): Promise<string> {
  const res = await clerkFetch("/v1/allowlist_identifiers", {
    method: "POST",
    body: JSON.stringify({ identifier: email, notify: false }),
  });
  if (res.ok) {
    const data = (await res.json()) as { id: string };
    return data.id;
  }
  // 422 + duplicate_record → fetch the existing id.
  const text = await res.text();
  if (res.status === 422 && text.includes("duplicate")) {
    const existing = await findAllowlistIdByEmail(email);
    if (existing) return existing;
  }
  throw new Error(
    `Clerk addToAllowlist failed (HTTP ${res.status}): ${text.slice(0, 200)}`,
  );
}

export async function findAllowlistIdByEmail(
  email: string,
): Promise<string | null> {
  const res = await clerkFetch("/v1/allowlist_identifiers");
  if (!res.ok) return null;
  const items = (await res.json()) as Array<{
    id: string;
    identifier: string;
  }>;
  const lower = email.toLowerCase();
  const hit = items.find((i) => i.identifier.toLowerCase() === lower);
  return hit ? hit.id : null;
}

// Remove an allowlist identifier by its alid. Used by the expiry
// sweep when an approved invite goes 14+ days unredeemed.
export async function removeFromAllowlist(alid: string): Promise<void> {
  const res = await clerkFetch(`/v1/allowlist_identifiers/${alid}`, {
    method: "DELETE",
  });
  // 404 means it's already gone — treat as success so the sweep is
  // idempotent against partial-failure scenarios.
  if (!res.ok && res.status !== 404) {
    const text = await res.text();
    throw new Error(
      `Clerk removeFromAllowlist failed (HTTP ${res.status}): ${text.slice(0, 200)}`,
    );
  }
}
