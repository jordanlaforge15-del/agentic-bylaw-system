// Auth-lifecycle fixtures for the e2e suite.
//
// Specs under web/e2e/auth/ simulate the Clerk sign-up → admin-approval
// → first-login → logout/login lifecycle without hosting Clerk. The
// fixtures below give specs:
//
//   * A unique, per-test synthetic identity (mintTestIdentity).
//   * A real "request an invite" call against the marketing form
//     (submitInviteRequest, optionally driving the /signup UI).
//   * A direct, no-Clerk approval write into invite_request via the
//     test-only FastAPI endpoint (approveInviteForEmail).
//   * Sign-in / sign-out via the cookie pair the proxy + advisor-auth
//     library now honour (signInAs / signOut).
//
// The seam pieces:
//
//   * Cookie ``abs_test_sub_user_id`` → ``X-Test-User-Id`` header
//   * Cookie ``abs_test_sub_email``   → ``X-Test-User-Email`` header
//                                       (used by the e2e backend's
//                                       invite-redemption code path)
//   * Cookie ``abs_test_sub_full_name`` → ``X-Test-User-Full-Name``
//   * Cookie ``abs_demo`` (existing) → proxy.ts password gate
//
// "Logout" clears all four cookies; the next /app navigation now
// redirects to /access (the password-gate page), matching the user-
// visible behavior of Clerk's sign-out from the user's perspective.
// "Login" re-mints abs_demo + the identity cookies, so the next
// request lands as the same advisor_user row (or a fresh one if the
// caller minted a new identity in between).

import {
  test as base,
  expect,
  type BrowserContext,
  type Page,
} from "@playwright/test";

export const E2E_BASE_URL =
  process.env.E2E_BASE_URL || "http://localhost:3001";

export const E2E_API_URL =
  process.env.E2E_API_URL || "http://127.0.0.1:8001";

const E2E_DEMO_PASSWORD =
  process.env.E2E_DEMO_PASSWORD || "e2e-demo-pw";

const COOKIE_NAMES = [
  "abs_demo",
  "abs_test_sub_user_id",
  "abs_test_sub_email",
  "abs_test_sub_full_name",
] as const;

export type TestIdentity = {
  /** Stable id sent as ``X-Test-User-Id`` — also the User row's
   * clerk_user_id once the e2e backend JIT-creates it. */
  subUserId: string;
  /** Email used by the invite-redemption match. Lowercased. */
  email: string;
  /** Display name; optional, becomes ``X-Test-User-Full-Name``. */
  fullName: string;
};

let _counter = 0;
function nextSlug(): string {
  _counter += 1;
  return `${Date.now().toString(36)}-${_counter.toString(36)}-${Math.random()
    .toString(36)
    .slice(2, 6)}`;
}

/** Mint a unique synthetic identity for one spec. The values are
 * deterministic per call within the process but globally unique
 * across parallel workers (counter + Date.now + random suffix). */
export function mintTestIdentity(prefix = "auth"): TestIdentity {
  const slug = nextSlug();
  return {
    subUserId: `${prefix}-${slug}`,
    email: `${prefix}-${slug}@e2e.test`,
    fullName: `Auth Test ${slug}`,
  };
}

/** Mint the password-gate cookie that ``web/proxy.ts`` checks before
 * letting requests through to /app and /admin. Equivalent to the
 * authedContext fixture in the legacy test-env file, but invokable
 * on demand here so a spec can mint/clear/mint again to simulate
 * sign-out → sign-in. */
async function mintAbsDemoCookie(context: BrowserContext): Promise<void> {
  const res = await context.request.post(`${E2E_BASE_URL}/api/access`, {
    data: { gate: "demo", password: E2E_DEMO_PASSWORD },
  });
  if (!res.ok()) {
    throw new Error(
      `failed to mint abs_demo cookie: HTTP ${res.status()} ${await res.text()}`,
    );
  }
}

/** Set the per-test identity cookies. Each cookie is scoped to the
 * dev server's host so it survives navigation between /app, /cases,
 * /signup. */
async function setIdentityCookies(
  context: BrowserContext,
  identity: TestIdentity,
): Promise<void> {
  const url = new URL(E2E_BASE_URL);
  await context.addCookies([
    {
      name: "abs_test_sub_user_id",
      value: identity.subUserId,
      domain: url.hostname,
      path: "/",
      httpOnly: false,
      secure: false,
      sameSite: "Lax",
    },
    {
      name: "abs_test_sub_email",
      value: identity.email,
      domain: url.hostname,
      path: "/",
      httpOnly: false,
      secure: false,
      sameSite: "Lax",
    },
    {
      name: "abs_test_sub_full_name",
      value: identity.fullName,
      domain: url.hostname,
      path: "/",
      httpOnly: false,
      secure: false,
      sameSite: "Lax",
    },
  ]);
}

/** "Sign in" the supplied identity into the current browser context.
 * Mints the password gate cookie and the three identity cookies, so
 * the next request from this context reaches the backend with
 * ``X-Test-User-Id: <identity.subUserId>`` and a matching
 * ``X-Test-User-Email``. The backend's user-dependency JIT-creates the
 * advisor_user row + redeems any approved InviteRequest by email on
 * first sight (see ``_resolve_or_create_test_user``). */
export async function signInAs(
  context: BrowserContext,
  identity: TestIdentity,
): Promise<void> {
  await mintAbsDemoCookie(context);
  await setIdentityCookies(context, identity);
}

/** "Sign out" the current browser context. Clears the password-gate
 * cookie and the identity cookies, leaving the context in the same
 * shape as a fresh browser. The next navigation to /app will
 * redirect through /access (the password gate's login page), and
 * subsequent API calls fall back to the default ``demo-user-1``
 * identity until signInAs is called again. */
export async function signOut(context: BrowserContext): Promise<void> {
  for (const name of COOKIE_NAMES) {
    await context.clearCookies({ name });
  }
}

/** Submit a request-invite via the public /api/invite route. By
 * default we POST directly so the spec doesn't get bogged down in
 * the form's React state; pass ``viaUi=true`` if a spec wants to
 * exercise the marketing form too. The returned invite id matches
 * the id stored in the database for the row created by this call. */
export async function submitInviteRequest(
  context: BrowserContext,
  args: {
    email: string;
    name: string;
    role?: string;
    project?: string;
    viaUi?: boolean;
    page?: Page;
  },
): Promise<{ id: string }> {
  const role = args.role ?? "Architect";
  const project =
    args.project ??
    "Halifax single-family lot, looking at ADU feasibility on a peninsula RC-1 zone.";

  if (args.viaUi) {
    if (!args.page) {
      throw new Error("submitInviteRequest({ viaUi: true }) requires `page`");
    }
    const page = args.page;
    await page.goto(`${E2E_BASE_URL}/signup`);
    await page
      .getByPlaceholder("you@firm.com")
      .fill(args.email);
    await page.getByPlaceholder("Your name").fill(args.name);
    await page
      .getByPlaceholder(
        "One project, one paragraph. The address or zone is helpful.",
      )
      .fill(project);
    await page.getByRole("button", { name: /Request invite/ }).click();
    const confirmation = page.getByText(/CONFIRMATION · #/);
    await expect(confirmation).toBeVisible({ timeout: 10_000 });
    const text = await confirmation.textContent();
    const match = text?.match(/CONFIRMATION · #(\S+)/);
    if (!match) {
      throw new Error(
        `submitInviteRequest: could not parse confirmation id from "${text}"`,
      );
    }
    return { id: match[1] };
  }

  const res = await context.request.post(`${E2E_BASE_URL}/api/invite`, {
    data: {
      email: args.email,
      name: args.name,
      role,
      project,
    },
  });
  if (!res.ok()) {
    throw new Error(
      `submitInviteRequest failed: HTTP ${res.status()} ${await res.text()}`,
    );
  }
  const data = (await res.json()) as { id: string };
  return { id: data.id };
}

/** Approve an invite for the given email, gifting starter credits at
 * the requested tier on first sign-in. Drops any prior invite row
 * for the email so re-running a spec doesn't trip the UNIQUE
 * constraint — the test backend handles that, see
 * ``advisor.api.e2e_server._mount_test_router``. */
export async function approveInviteForEmail(
  context: BrowserContext,
  args: {
    email: string;
    name: string;
    starter_credits?: number;
    starter_tier?: "quick" | "standard" | "complex";
  },
): Promise<void> {
  const res = await context.request.post(
    `${E2E_API_URL}/v1/_test/invite-approve`,
    {
      data: {
        email: args.email,
        name: args.name,
        starter_credits: args.starter_credits ?? 5,
        starter_tier: args.starter_tier ?? "standard",
      },
    },
  );
  if (!res.ok()) {
    throw new Error(
      `approveInviteForEmail failed: HTTP ${res.status()} ${await res.text()}`,
    );
  }
}

/** Open a case via the FastAPI test server using the current test
 * identity. The identity cookies have already been minted by
 * signInAs, but ``context.request`` cannot forward cookies to a
 * cross-origin URL (FastAPI at :8001 vs Next at :3001) — so this
 * helper sets ``X-Test-User-Id`` (and email) directly. The returned
 * caseId can be used to jump into /app?case_id=N without driving the
 * marketing form. */
export async function openCaseAsIdentity(
  context: BrowserContext,
  identity: TestIdentity,
  opts: {
    anchorLabel?: string;
    anchorKind?: "address" | "project_ref" | "development_application";
    tier?: "quick" | "standard" | "complex";
  } = {},
): Promise<{ caseId: number }> {
  const {
    anchorLabel = `auth-${nextSlug()}`,
    anchorKind = "address",
    tier = "standard",
  } = opts;
  const res = await context.request.post(`${E2E_API_URL}/v1/cases`, {
    headers: {
      "Content-Type": "application/json",
      "X-Test-User-Id": identity.subUserId,
      "X-Test-User-Email": identity.email,
      "X-Test-User-Full-Name": identity.fullName,
    },
    data: {
      anchor_label: anchorLabel,
      anchor_kind: anchorKind,
      tier,
    },
  });
  if (!res.ok()) {
    throw new Error(
      `openCaseAsIdentity failed: HTTP ${res.status()} ${await res.text()}`,
    );
  }
  const data = (await res.json()) as { case: { id: number } };
  return { caseId: data.case.id };
}

/** Accept the current Terms and Conditions on behalf of the supplied
 * identity. The /app gate (ABS-18) redirects any user who hasn't
 * accepted the live T&C version to /app/terms, which masks the chat
 * composer the auth specs assert on. The full T&C UI flow is covered
 * separately by web/e2e/functional/terms-acceptance-gate.spec.ts —
 * here we just clear the gate via the JSON API so the auth specs
 * stay focused on the identity / case / chat lifecycle.
 *
 * Idempotent: re-accepting an already-recorded version is a no-op
 * thanks to the (user_id, version_hash) unique constraint inside
 * record_acceptance. The backend JIT-creates the advisor_user row on
 * GET /v1/terms/current, so this helper safely runs before the first
 * /v1/cases request. */
export async function acceptCurrentTermsAs(
  context: BrowserContext,
  identity: TestIdentity,
): Promise<void> {
  const headers = {
    "X-Test-User-Id": identity.subUserId,
    "X-Test-User-Email": identity.email,
    "X-Test-User-Full-Name": identity.fullName,
  };
  const currentRes = await context.request.get(
    `${E2E_API_URL}/v1/terms/current`,
    { headers },
  );
  if (!currentRes.ok()) {
    throw new Error(
      `acceptCurrentTermsAs: GET /v1/terms/current failed: HTTP ${currentRes.status()} ${await currentRes.text()}`,
    );
  }
  const current = (await currentRes.json()) as { version: string; accepted: boolean };
  if (current.accepted) return;
  const acceptRes = await context.request.post(
    `${E2E_API_URL}/v1/terms/accept`,
    {
      headers: { ...headers, "Content-Type": "application/json" },
      data: { version: current.version },
    },
  );
  if (!acceptRes.ok()) {
    throw new Error(
      `acceptCurrentTermsAs: POST /v1/terms/accept failed: HTTP ${acceptRes.status()} ${await acceptRes.text()}`,
    );
  }
}

/** Wait for an assistant message containing the expected text in the
 * chat thread. Matches ``waitForAssistantText`` from the legacy
 * fixture but lives here so auth specs don't have to import from
 * two places. */
export async function waitForAssistantText(
  page: Page,
  expected: string | RegExp,
  opts: { timeout?: number } = {},
): Promise<void> {
  const re = expected instanceof RegExp ? expected : new RegExp(expected);
  await expect(page.locator("[data-testid='chat-thread']"))
    .toContainText(re, { timeout: opts.timeout ?? 15_000 });
}

// Re-export Playwright primitives so specs can import everything from
// this file. NB: unlike fixtures/test-env.ts there is no auto cookie
// mint here — each spec controls its identity lifecycle explicitly,
// which is the whole point of this suite.
export const test = base;
export { expect };
