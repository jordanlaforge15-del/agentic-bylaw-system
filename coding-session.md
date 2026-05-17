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
