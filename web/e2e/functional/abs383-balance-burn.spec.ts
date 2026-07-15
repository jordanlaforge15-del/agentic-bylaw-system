// Functional: ABS-383 — chat pre-flight token wallet.
//
// The chat route bills the account token wallet (not case credits). This
// spec drives the real Next.js proxy end to end (browser → /api/chat →
// FastAPI) for a freshly minted identity and asserts the two
// user-observable behaviours the pivot introduces:
//
//   1. A chat turn visibly DECREMENTS the balance: the SSE stream carries a
//      per-turn ``token_balance`` event whose ``burned_tokens`` > 0, and the
//      wallet read API reflects the same post-burn balance.
//   2. When the wallet is exhausted (driven negative in a single turn via the
//      ``MOCK_BURN_ALL`` dispatcher keyword), the NEXT turn is refused with a
//      402 ``insufficient_tokens`` at the API layer — before any stream opens.
//
// Each run mints a unique identity so it never races the shared demo user's
// wallet. The e2e stack runs billing DORMANT, so the wallet read + chat burn
// are both served without Stripe wired.

import {
  acceptCurrentTermsAs,
  expect,
  mintTestIdentity,
  openCaseAsIdentity,
  signInAs,
  test,
} from "../auth/fixtures";

// Design-spec default the e2e FastAPI env does not override.
const SIGNUP_GRANT = 25_000;

type SseEvent = { event: string; data: string };

function parseSse(body: string): SseEvent[] {
  const events: SseEvent[] = [];
  let cur: Partial<SseEvent> = {};
  for (const raw of body.split("\n")) {
    const line = raw.trimEnd();
    if (line === "") {
      if (cur.event && cur.data !== undefined) {
        events.push(cur as SseEvent);
      }
      cur = {};
      continue;
    }
    if (line.startsWith("event:")) cur.event = line.slice("event:".length).trim();
    else if (line.startsWith("data:")) cur.data = line.slice("data:".length).trim();
  }
  if (cur.event && cur.data !== undefined) events.push(cur as SseEvent);
  return events;
}

test("chat turn burns wallet tokens and streams a token_balance decrement", async ({
  context,
}) => {
  const identity = mintTestIdentity("abs383");
  await signInAs(context, identity);
  await acceptCurrentTermsAs(context, identity);

  // Fresh identity starts at the signup grant.
  const before = await context.request.get("/api/billing/wallet");
  expect(before.status(), await before.text()).toBe(200);
  const beforeWallet = (await before.json()) as { balance_tokens: number };
  expect(beforeWallet.balance_tokens).toBe(SIGNUP_GRANT);

  const { caseId } = await openCaseAsIdentity(context, identity, {
    anchorLabel: `abs383-${identity.subUserId}`,
  });

  const res = await context.request.post("/api/chat", {
    data: {
      message: "What is the minimum front yard setback?",
      case_id: caseId,
    },
  });
  expect(res.status(), await res.text()).toBe(200);
  const events = parseSse(await res.text());

  const tb = events.find((e) => e.event === "token_balance");
  expect(tb, `expected a token_balance SSE event, got ${events.map((e) => e.event)}`).toBeTruthy();
  const payload = JSON.parse(tb!.data) as {
    balance_tokens: number;
    burned_tokens: number;
    approx_turns_remaining: number;
    low_balance: boolean;
    warn_threshold_tokens: number;
  };
  expect(payload.burned_tokens).toBeGreaterThan(0);
  expect(payload.balance_tokens).toBe(SIGNUP_GRANT - payload.burned_tokens);
  expect(payload.balance_tokens).toBeLessThan(SIGNUP_GRANT);
  // Backend-owned turns conversion + threshold fields are present.
  expect(typeof payload.approx_turns_remaining).toBe("number");
  expect(typeof payload.warn_threshold_tokens).toBe("number");

  // The wallet read API reflects the same post-burn balance.
  const after = await context.request.get("/api/billing/wallet");
  const afterWallet = (await after.json()) as { balance_tokens: number };
  expect(afterWallet.balance_tokens).toBe(payload.balance_tokens);
});

test("exhausted wallet: next turn is refused with 402 insufficient_tokens", async ({
  context,
}) => {
  const identity = mintTestIdentity("abs383oot");
  await signInAs(context, identity);
  await acceptCurrentTermsAs(context, identity);

  const { caseId } = await openCaseAsIdentity(context, identity, {
    anchorLabel: `abs383oot-${identity.subUserId}`,
  });

  // Turn 1: MOCK_BURN_ALL overdraws the signup grant in a single turn, so
  // the wallet goes negative (below the floor).
  const drain = await context.request.post("/api/chat", {
    data: { message: "MOCK_BURN_ALL — drain the wallet", case_id: caseId },
  });
  expect(drain.status(), await drain.text()).toBe(200);
  const drained = parseSse(await drain.text()).find(
    (e) => e.event === "token_balance",
  );
  expect(drained).toBeTruthy();
  const drainedBalance = (JSON.parse(drained!.data) as { balance_tokens: number })
    .balance_tokens;
  expect(drainedBalance).toBeLessThanOrEqual(0);

  // Turn 2: pre-flight floor refuses BEFORE any stream opens. The chat
  // proxy passes the upstream 402 status + body through verbatim.
  const refused = await context.request.post("/api/chat", {
    data: { message: "and now?", case_id: caseId },
  });
  expect(refused.status()).toBe(402);
  const body = (await refused.json()) as {
    detail: {
      code: string;
      balance_tokens: number;
      floor_tokens: number;
      approx_turns_remaining: number;
    };
  };
  expect(body.detail.code).toBe("insufficient_tokens");
  expect(body.detail.balance_tokens).toBeLessThanOrEqual(body.detail.floor_tokens);
  expect(body.detail.approx_turns_remaining).toBe(0);
});
