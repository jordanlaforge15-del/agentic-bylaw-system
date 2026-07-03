# Follow-up model — grounded conversation continuation over a refinement-window economy

**Date:** 2026-07-03 · **Status:** ACCEPTED
**Ticket:** ABS-347 — "Follow-up model: grounded report→conversation
continuation vs. refinement-only window."
**Builds on:** `docs/decisions/2026-06-priced-question-catalog.md` (Buy an
Answer — the priced-question SKU whose economics this decision preserves).
**Governs the build in:** ABS-344 — "Unify report + conversation in one case
workspace (Report·Conversation toggle + CaseToolbar)."

## The divergence this resolves

The spec (README `#app` / `design/abs-website/design_files/app-screen.jsx`) makes
conversation the product: a "reading" drops into a full chat thread grounded in
the report + parcel, on the **same** case, flipped via a `Report · Conversation`
toggle. "Both are the same workspace."

The implementation instead ships a **bounded refinement window** on a standalone
answer page (`web/components/product/answer-view.tsx`): N free follow-ups scoped
to one purchased answer, then "buy a new answer," with upstream `409` guardrails
(`src/advisor/billing/answers.py`) routing a materially-different question
(`new_question`) or an exhausted window (`window_exhausted`) to a fresh purchase.
Today reports (`QuestionPurchase`) and conversations (`Case` / `ChatSession`)
are also **disjoint at the data layer** — a report carries its subject in
`inputs_json`, opens no case, and the sidebar merges the two only visually,
navigating a report row *out* to `/app/answers/{id}`.

These are two different interaction models. This is the product decision.

## Decision

**Adopt grounded conversation continuation as the follow-up presentation, and
keep the bounded refinement window as the billing boundary.** Not either/or —
the conversation *is* the refinement window, rendered as a thread instead of a
single-shot composer. The three candidate options map as:

- ❌ **Keep refinement-only.** Rejected: it contradicts the spec's core premise
  ("the chat is the product") and the unification already committed in ABS-344,
  and it strands the report on a disconnected route away from the parcel/sources
  panes.
- ❌ **Open-ended conversation continuation.** Rejected: a fully free-form thread
  breaks the "one grounded answer, one charge" monetization (ABS-312). A user
  could ask unlimited *materially new* questions for free inside a single
  purchased thread. The per-question price would stop anchoring to the value
  delivered.
- ✅ **Conversation continuation, bounded by the refinement window (this
  decision).** The report opens inside the `/app` workspace and drops into a
  grounded conversation; the existing refinement-window economics gate how far
  that conversation runs for free before a new purchase is required.

## The refinement window's relationship to the conversation (the contract)

The refinement window is **retained unchanged as the economic boundary** and is
re-expressed as the report-backed conversation's free-follow-up budget. Nothing
in `src/advisor/billing/answers.py` needs to relax; only its *surface* changes
(single composer → thread), which ABS-344 builds.

1. **A report-backed conversation is grounded continuation.** Each follow-up is a
   `run_refinement` turn (`answers.py`), seeded with the purchased answer's
   transcript + parcel context, so it continues the *same* grounded answer rather
   than starting a fresh one. The prior answer's citations survive into the
   follow-up (this is the defining property — see e2e).
2. **The window is the free budget.** Each grounded follow-up consumes one
   refinement turn, bounded by `MAX_REFINEMENTS` within `WINDOW_HOURS`.
   `refinements_remaining` is surfaced as the conversation's remaining free
   follow-ups (in the CaseToolbar / composer, per ABS-344).
3. **`new_question` → new purchase, surfaced in-thread.** A materially-different
   subject (new address / use / determination) is still detected and *not* served
   free; it is surfaced inline as a conversation system message routing to the
   question menu, instead of a standalone notice card. This is the guardrail that
   protects the per-question price.
4. **`window_exhausted` → buy a new answer, surfaced in-thread.** When the
   follow-up budget or the time window is spent, the thread stays readable
   (history preserved) but new grounded follow-ups require a new purchase; the
   composer surfaces the "buy a new answer" dead-end.
5. **Toggle visibility (ABS-344).** The `Report · Conversation` toggle appears
   only for report-backed cases; a conversation-only case (opened from chat, no
   purchase) has no report pane and no toggle, and is *not* subject to the
   refinement window — that window is a property of a purchased answer, not of
   chat.

In one line: **conversation continuation is the UX; the refinement window is the
meter.** The spec's presentation is adopted without giving away the monetization.

## Scope split with ABS-344

- **ABS-347 (this ticket)** records the decision, defines the economics +
  grounding contract above, and provides e2e that asserts the chosen model at the
  contract level (grounded continuation + window-as-boundary).
- **ABS-344** builds the workspace UI that realizes it: the `CaseToolbar`
  Report·Conversation toggle, the shared left case-list / right Sources·Parcel
  panes around the report, the `REPORT / CONVERSATION / GENERATING` header label,
  and the report+parcel context system line that seeds the conversation. Linking
  a `QuestionPurchase` to a `Case` at the data layer (so the two products stop
  being disjoint) is part of that build.

## Consequences

- The billing model (ABS-312 priced-question catalog) is unchanged — this is a
  presentation + workspace change, not a pricing change. No new charge type, no
  change to `MAX_REFINEMENTS` / `WINDOW_HOURS` / the `new_question` gate.
- The standalone `/app/answers/[id]` refinement surface is superseded by the
  in-workspace conversation once ABS-344 lands; until then it remains the live
  follow-up surface and continues to enforce the identical contract.
- Follow-up copy shifts from "refine this answer" toward "continue the
  conversation about this parcel (N free follow-ups left)" — the same window,
  framed as a conversation rather than a bounded edit. Copy/testid changes on the
  shared surface belong to the ABS-344 build to avoid churn on an in-flight file.
</content>
</invoke>
