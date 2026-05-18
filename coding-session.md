---
Title: ABS-9 test follow-up — Playwright specs for case-open credit adoption & network error
Description: Add instrumented Playwright tests covering both halves of the ABS-9 fix that shipped on 2026-05-17 (commits 1240f0b/ca3778e on dev, since promoted to main).

UI side — web/e2e/functional/case-open-network-error.spec.ts (1 test, new):
Uses page.route + route.abort('failed') to deterministically reproduce the Safari "Load failed" / dropped-connection rejection on POST /api/cases. Asserts the new "Network error opening case: …" banner appears, the Open-case button re-enables, and the URL stays on /cases/new.

Backend side — web/e2e/functional/abs9-credit-adoption.spec.ts (1 test, relocated + patched):
Originally drafted untracked on dev. Moved into this branch and fixed: the existing assertion `expect(chatBody).toMatch(/RC-LUB §15\.4/)` couldn't match because the SSE wire format JSON-escapes § as `§`. Added a decodeAssistantText helper that parses each `data:` line as JSON and concatenates the `content_block_delta.text_delta` payloads, then asserts the citation against the decoded text. The seed step uses a fresh user with exactly 1 credit per tier, so the original 402 symptom would actually fire if the ABS-9 fix were reverted.

Dropped from a prior iteration: web/e2e/functional/case-open-credit-adoption.spec.ts — relied on the shared 200-credit demo user so it didn't differentiate broken from fixed code. The relocated dev spec covers the same regression more rigorously.

Branch: worktree-abs-9-playwright-tests (off dev, per docs/BRANCHING_STRATEGY.md).
Start Date: 2026-05-18
---
Title: ABS-9 — Fix network error when creating a new case
Description: User norman.stephanie@gmail.com in prod hit "Network error: load failed" when creating a new case. Root cause: a 402 on the first /v1/chat after /v1/cases — open_case reserves a credit against the case (session_id=NULL), and the chat route's reserve_credit_for_session ignored that and claimed another available credit, 402'ing users who'd just spent their last credit on the case open. Fix: chat now adopts the case-reserved credit. Also added a try/catch on the frontend openCase fetch so a real network error surfaces instead of being swallowed.
Start Date: 2026-05-17
Merged Into Main: Yes
Date Merged: 2026-05-17
---
Title: ABS-8 — Case creation 402 in prod (idempotency + leaked-credit recovery)
Description: chris.rafuse@gmail.com hit a 402 ({code: no_available_credit, tier: standard}) on POST /v1/cases despite having been granted 3 standard credits. Root cause is the ABS-11 double-reservation bug — two of his three credits were stuck in `reserved` state with session_id=NULL, attached to cases that already had a sessioned credit. ABS-9 closed the chat-side leak (the second reservation) but did not address (a) the equivalent leak path on a duplicate POST /v1/cases for the same anchor, or (b) the credits already leaked in prod. This change adds a tier-matched idempotency guard in open_case so a re-open returns the case's existing active credit instead of claiming a fresh one, plus a refund_orphaned_case_reservations sweep + admin endpoint POST /v1/admin/maintenance/refund-orphaned-reservations that conservatively refunds reserved-no-session credits whose case has a sibling sessioned active credit at the same tier (the diagnostic signature of the pre-ABS-9 leak). New audit event credit_reused distinguishes idempotent re-open from a fresh reservation. Recovery for credit 9 (no sibling — case 4 has no other active credit) falls to the existing 24h abandon sweep or a manual admin grant.
Start Date: 2026-05-17
Merged Into Main: No
Date Merged: —
---
