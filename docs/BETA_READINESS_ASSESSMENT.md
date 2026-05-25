# Beta Readiness Assessment

**Date:** 2026-05-25
**Scope:** Public beta via marketing-driven trial signups
**Assessed by:** ABS-118 readiness audit

---

## Executive Summary

The application is architecturally sound and has strong foundations for a
public beta: comprehensive legal documentation, proper auth gating,
multi-layer rate limiting, and a polished responsive UI with no dead
pages. However, there are **critical gaps** in liability protection
(incorporation, T&C legal review), user feedback mechanisms, availability
monitoring, and error observability that must be addressed before
marketing-driven trials begin.

**Readiness verdict:** NOT YET READY — 9 must-fix items, 8 should-fix items.

---

## 1. User Account Management

**Status: READY**

Account management is fully delegated to Clerk's `UserButton` component,
accessible from the sidebar (`web/components/product/sidebar.tsx:214`)
and top nav (`web/components/product/top-nav.tsx:123`).

| Capability | Status | Mechanism |
|------------|--------|-----------|
| View/edit profile | Done | Clerk UserButton dropdown |
| Change email/password | Done | Clerk account settings |
| Delete account | Done | Clerk account management |
| View usage/billing history | Done | `/billing` page — credits, purchases, cases |
| Sign out | Done | Clerk UserButton |

**Gap:** No account data export (GDPR "right to portability"). Low
priority for NS-only beta but needed before any EU-resident signups.

---

## 2. Liability Mitigation

### 2.1 Terms of Service

**Status: NEEDS WORK — 3 must-fix items**

The T&C document (`docs/TERMS_AND_CONDITIONS.md`) is comprehensive and
covers the critical liability areas:

- §3: "Not legal, professional, or expert advice" with exhaustive
  relationship disclaimers
- §4: "Mandatory verification; no unreasonable reliance" — user bears
  sole responsibility
- §5: AI-specific disclaimers (hallucinations, wrong-zone, citation
  fabrication, stale content, numeric/geometric errors, MCP downstream
  transformations)
- §6: AS-IS/AS-AVAILABLE, no warranties, NS Consumer Protection Act
  carve-out
- §7: Liability cap at max(fees paid in 12 months, CAD $100)

**Must-fix items:**

1. **Bracketed placeholders remain unfilled** — effective date, last-updated
   date, mailing address, and privacy-policy URL are all `[Insert ...]`
   (lines 3–4, plus body references).
2. **Legal counsel review required** — the document header explicitly
   states: "Pre-counsel review draft. This document has not been reviewed
   by a Nova Scotia lawyer." The consumer-side enforceability of §7
   (liability cap) and §17 (class-action waiver) under the NS Consumer
   Protection Act needs counsel sign-off before any paying customer.
3. **Business entity is "unincorporated sole proprietorship"** (§1,
   line 23) — see §2.2 below.

### 2.2 Limited Liability Incorporation

**Status: NOT DONE — must-fix**

The T&C describe the Provider as "an unincorporated business operated as
a sole proprietorship." For a public beta offering that processes
municipal bylaw interpretations, the personal liability exposure is
significant. A limited liability entity (NS corporation or federal
corporation registered extra-provincially) should be established and
should be the contracting party in the T&C before public signups.

### 2.3 Disclaimers Throughout the App

**Status: GOOD — 1 gap**

Existing disclaimers are well-placed:

- **Advisory-only banner** (`web/components/product/advisory-only-banner.tsx`):
  Yellow-bordered "Advisory only — not an approval of record" on all
  submission pages. Uses `role="note"` for accessibility.
- **Chat system message**: "Do not present a reading to a third party as
  professional advice from us" on session connect.
- **Terms acceptance gate**: Click-wrap on first sign-in
  (`web/app/(product)/app/terms/page.tsx`) recorded in
  `advisor_terms_acceptance` with version tracking.
- **Support page**: FAQ explicitly says "ABS is a research tool — always
  verify with HRM Planning."

**Gap:** The main chat interface (`/app`) does not show a persistent
disclaimer banner. The advisory-only banner appears on submissions pages
but not the primary chat flow where most users will interact. A compact
"research tool — not legal advice" reminder should be visible on the
chat page.

### 2.4 Accuracy & Validity Testing

**Status: PARTIAL**

- Layer 2 has an evaluation harness (`src/layer2/eval/`) for testing
  retrieval and reasoning quality.
- Pilot scorecard exists (`docs/pilot/pilot-scorecard.md`) for the
  Halifax pilot.
- E2E tests cover functional flows but not answer accuracy.
- No systematic ground-truth evaluation against known bylaw
  interpretations has been documented as complete.

---

## 3. Feedback

**Status: BACKEND EXISTS, NO UI — must-fix**

### Backend

A structured feedback system exists in Layer 2
(`src/layer2/feedback/service.py`) with three granularity levels:

- **Answer feedback**: rating, is_correct, is_incomplete, notes
- **Claim feedback**: is_correct, corrected_value_text, corrected JSON,
  reviewer type (triggers verification status update)
- **Retrieval feedback**: missing fragments, irrelevant fragments, notes

### Frontend

**No feedback UI exists in the chat interface.** The support page
(`web/app/(marketing)/support/page.tsx`) mentions a "flag control inside
the reading" but no such component is implemented in the chat thread or
chat shell components.

**Required for beta:**

1. **Thumbs up/down on chat messages** — minimum viable feedback that
   connects to `submit_answer_feedback()`.
2. **Flag/report button** on individual messages for detailed feedback
   (bad citation, wrong zone, hallucination).
3. **General feedback form** — accessible from the app shell for
   non-message-specific feedback (UX issues, feature requests).

---

## 4. User Analytics

**Status: PARTIAL — admin dashboard exists, needs expansion**

### What Exists

- **Admin analytics** (`/admin/analytics`): tier distribution across
  credit types and upgrade funnel conversion (classifier → offered →
  accepted/declined).
- **Usage audit trail**: `advisor_usage_event` table logs every LLM call,
  rate limit hit, and credit consumption with timestamps.
- **Case event log**: `advisor_case_event` table records state transitions
  with JSON payloads.

### What's Missing

- **Aggregate usage dashboard**: daily/weekly active users, sessions per
  user, messages per session, retention cohorts.
- **Query analytics**: most common question types, geographic
  distribution of parcels queried, topic clustering.
- **Revenue/credit metrics**: credit consumption rate, average credits
  per user, time to first purchase.
- **Funnel visualization**: signup → terms acceptance → first question →
  repeat usage → purchase.

---

## 5. Availability Monitoring

**Status: NOT IMPLEMENTED — must-fix**

### Current State

- **Health endpoint**: `GET /healthz` returns `{"status": "ok"}` without
  checking database connectivity, LLM API reachability, or any
  dependency (`src/advisor/api/app.py:405–407`).
- **No external monitoring**: no UptimeRobot, Pingdom, or equivalent
  configured.
- **No alerting**: no Slack webhook, PagerDuty, or email notification
  for downtime.
- **No SLIs/SLOs defined**: no latency, availability, or error rate
  targets documented.

### What's Needed

1. **Deep health check**: `/healthz` should verify DB connectivity (a
   lightweight `SELECT 1`) and return degraded status if downstream
   services are unreachable.
2. **External uptime probe**: an external service polling `/healthz`
   every 60s with alerting on 2+ consecutive failures.
3. **Slack/email alerts**: notify the operator within 5 minutes of a
   detected outage.
4. **Basic SLOs**: define targets (e.g., 99.5% availability, p95 chat
   latency < 10s) so there's a measurable bar.

---

## 6. Dead Pages

**Status: READY — no dead pages found**

All 28 `page.tsx` routes are fully implemented:

- **Marketing**: home, about, pricing, coverage, support, terms, privacy,
  changelog, signup, login
- **Product**: app (chat), terms acceptance, cases, billing, submissions
- **Admin**: analytics, invites, credits, cases
- **Auth**: sign-in, sign-up, access gate

The `/changelog` page is a beta placeholder directing users to the git
history, which is honest and appropriate.

---

## 7. Security Assessment

**Status: STRONG — 2 items to address**

### Strengths

| Area | Finding |
|------|---------|
| **Auth** | Clerk JWKS-based JWT validation; X-Test-User-Id header properly gated to dev/test only |
| **SQL injection** | SQLAlchemy ORM parameterized queries throughout; no raw SQL |
| **Input validation** | Pydantic `Field()` constraints on all user-facing endpoints |
| **Rate limiting** | Three layers: Caddy IP-based (120/min global, 10/min chat, 5/min auth), per-user RPM (6/min) |
| **HTTPS** | HSTS with 1-year max-age + includeSubDomains via Caddy |
| **Security headers** | X-Content-Type-Options, X-Frame-Options DENY, Referrer-Policy, Server header stripped |
| **Admin routes** | Fail-closed Clerk ID allowlist at both Next.js and FastAPI layers |
| **Webhook verification** | Svix signature validation on Clerk webhooks |

### Items to Address

1. **No dependency vulnerability scanning** — neither `pip audit` nor
   `npm audit` runs in the build pipeline. Dependencies are modern and
   pinned, but automated scanning should be added.
2. **No React error boundaries** — no `error.tsx` or `not-found.tsx`
   pages exist. Unhandled React errors will show a blank page or the
   default Next.js error screen. Error boundaries should catch and
   display user-friendly messages while reporting to an error tracking
   service.

### Already Secure

- `.gitignore` properly excludes secrets (`*.pem`, `*.key`, `*_api_key`)
- Production requires `CLERK_JWKS_URL` — without it, auth falls back to
  warning-level log, not silent bypass
- CORS is restrictive in production (Caddy reverse proxy, no wildcard)

---

## 8. Error Observability

**Status: NOT IMPLEMENTED — must-fix**

- **No error tracking service** (Sentry, Rollbar, etc.) integrated on
  either frontend or backend.
- **Python logging** uses stdlib `logging.getLogger(__name__)` — no
  structured output (JSON), no trace correlation, no centralized
  aggregation.
- **Frontend errors** are not reported to any backend or external service.
- **No React error boundaries** — unhandled component errors crash to
  blank screen.

**Impact:** Production errors are invisible unless the operator
manually tails `docker compose logs`. For a marketing-driven beta,
this means user-facing failures can go undetected for hours.

---

## 9. Operational Readiness

**Status: PARTIAL**

| Concern | Status |
|---------|--------|
| CI/CD pipeline | Not automated — manual build, push, SSH deploy |
| Database backups | Manual `pg_dump` only — no cron, no cloud storage |
| Rollback procedure | Documented but manual (edit compose, pull, up) |
| Disaster recovery | Not tested |
| Log retention | Container stdout only — lost on rotation |

---

## Gap Summary

### Must-Fix (blocks public beta)

| # | Area | Gap | Priority |
|---|------|-----|----------|
| 1 | Liability | Establish limited liability entity (incorporation) | Urgent |
| 2 | Liability | Complete T&C legal counsel review + fill placeholders | Urgent |
| 3 | Feedback | Build chat message feedback UI (thumbs + flag) | High |
| 4 | Monitoring | Implement availability monitoring with alerting | High |
| 5 | Monitoring | Upgrade `/healthz` to deep health check | High |
| 6 | Observability | Integrate error tracking (Sentry or equivalent) | High |
| 7 | Observability | Add React error boundaries | High |
| 8 | Disclaimer | Add persistent disclaimer to main chat page | High |
| 9 | Security | Add dependency vulnerability scanning | High |

### Should-Fix (improves beta quality)

| # | Area | Gap | Priority |
|---|------|-----|----------|
| 10 | Analytics | Build aggregate usage dashboard (DAU, retention, funnel) | Medium |
| 11 | Feedback | Add general feedback form (non-message-specific) | Medium |
| 12 | Accuracy | Complete ground-truth evaluation for Halifax bylaws | Medium |
| 13 | Ops | Automate database backups to offsite storage | Medium |
| 14 | Ops | Set up basic CI/CD (test gate + auto-build on main) | Medium |
| 15 | Observability | Implement structured logging with centralized aggregation | Medium |
| 16 | UX | Add custom error.tsx and not-found.tsx pages | Medium |
| 17 | Analytics | Define and instrument SLIs/SLOs | Medium |
