# Product Strategy — Conversations Now, Artifacts Next (a combined model)

**Date:** 2026-06-12 · **Status:** PROPOSED (strategy; supersedes the framing
of the turn-pack work as the *whole* product)
**Inputs:**
- `docs/decisions/2026-06-pricing-v2-turn-packs.md` (the turn product)
- `docs/COST_MODEL.md` (measured $0.99 USD/turn economics)
- `docs/AI-Powered Land Use Bylaw Reports in Halifax_ Productization Catalog and Licensing Analysis.md` (the artifact catalog + licensing analysis; senior-planner-sourced)

## Thesis

We have two viable product shapes over the *same* engine (the citation-grounded
retrieval MCP + Layer-2 compliance evaluator):

- **Conversations** — metered *turns*. Low friction, exploratory, self-serve.
  Answers a question; the user can interrogate it. This is what exists today.
- **Artifacts** — sold *deliverables* (variance package, zoning due-diligence
  memo, non-conforming-use analysis…). High value, anchored to consultant fees
  ($800–$2,500 per the catalog), bounded and predictable in cost.

These are **complementary, not competing**: conversation is exploration and
support; the artifact is the thing the customer pays real money for and walks
away with. The strategy is to **ship conversations first and add artifacts
incrementally**, using the conversation as the intake surface that feeds the
artifacts.

## Why artifacts are a better cost/pricing fit (and why we still start with turns)

The artifact model resolves the pricing tensions documented across
`COST_MODEL.md` and the turn-pack design:

| Dimension | Turn packs | Artifacts |
|---|---|---|
| Value anchor | App pricing (~$5–8/turn feels dear to a homeowner) | Consultant fee (thousands) — "$300 vs a $1,500 memo" |
| Taximeter anxiety | Present (per-question metering) | None — you buy a deliverable |
| Cost predictability | Open-ended turn; needs the cumulative breaker (ABS-305) | Bounded by construction (defined N-section document) |
| Margin | ~70–79% (Opus); ~95%+ (Sonnet) | ~95%+ ($5–15 cost vs $150–500 price) |
| Build cost | Low — billing rework on existing chat | High — composition + rendering + intake + QA platform |
| Liability | Low — conversational, interrogable | High — relied on in transactions |

We still **start with turns** because: (a) the build is mostly the billing rework
already specced (ABS-305…310); (b) it validates demand and accumulates the usage
data that tells us *which* artifacts to build first; (c) it builds corpus trust
before we attach a liability-bearing deliverable to the brand; and (d) the
conversation is the natural intake mechanism for the artifacts — see below.

## Technical feasibility of the artifact line (assessed against current code)

**The expensive part is built.** The retrieval/compliance engine is
production-shaped and citation-grounded:

- `evaluate_submission_against_bylaws` (`src/layer2/compliance/evaluator.py`)
  returns a per-attribute compliance matrix — `overall_status`, per-attribute
  `verdict`, `applicable_clauses` with `citation_path`, and `delta` shortfalls.
  **That is the analytical core of a variance application and a
  non-conforming-use memo.** A submissions router + `ApprovalDecision` ledger
  already sit behind it.
- `get_address_profile` → zone, height/FAR precincts, heritage, overlays, with
  citations (the parcel-context section, one call).
- `get_zone_profile` → dimensions, permitted/not-permitted uses, parking, with
  per-field confidence and citations.
- `scripts/pilot_variance_report.py` already prototyped the variance path to
  console — the path is proven, not productized.

**What must be built (engineering, not invention):**

| Gap | Effort | Note |
|---|---|---|
| Composition layer (orchestrate N grounded sections; today the advisor is single-question → single-answer, `tool_loop.py`) | Moderate | Deterministic multi-step fan-out over already-structured JSON. |
| Document rendering (PDF/DOCX) | Small–moderate | **No rendering lib wired in** today; `weasyprint`/`python-docx` are off-the-shelf. |
| Per-artifact intake (capture customer facts: setbacks, dimensions, dates) | Moderate | The crux — see gap #1 below. The conversation can carry this. |
| QA / verification gates for a *sold* deliverable | Moderate, load-bearing | Adversarial self-check, coverage/confidence signaling, disclaimers. Higher bar than chat. |
| Artifact storage, versioning, re-issue, export endpoint | Small–moderate | Submission storage exists; export/versioning don't. |

**Timeline (1 engineer):** ~2–3 months to the *first* artifact **plus the
platform underneath it** (the platform is most of that cost). Each additional
artifact reusing the engine + renderer: **~2–3 weeks**. Stage-1's low-discretion
set is a ~4–5 month line, front-loaded by the platform.

## Business gaps the raw catalog does not cover

1. **Intake / garbage-in (biggest).** The AI cannot *measure* a setback; it
   reasons over supplied facts. "Submission-ready" is bounded by input quality.
   *Mitigation in the combined model: the conversation gathers and sanity-checks
   the facts, then pre-populates the artifact intake.*
2. **Acceptance ≠ legality → the sellable launch catalog is narrower than 12
   items.** AI-authored work is procedurally accepted but carries less
   persuasive weight in *discretionary* matters. Near-term sellable = the
   **low-discretion five**: variance, zoning due-diligence, non-conforming use,
   as-of-right compliance check, and the triage/readiness report. Discretionary
   files (rezoning, DA, MPS, heritage, appeals) are consultant-accelerator only.
3. **Corpus freshness is a liability line item.** A report citing a superseded
   bylaw is an E&O event. Selling deliverables demands a freshness SLA + ongoing
   maintenance cost the conversational product could shrug off.
4. **Due-diligence positioning vs. HRM's $200 official letter.** Sell on speed
   (minutes vs 17–24 days) and as *preliminary*; this ceilings the price and
   demands airtight disclaimers.
5. **Channel.** Repeat buyers (realtors, lawyers, lenders) need a channel —
   direct? brokerage/MLS? referral partners? The catalog names buyers, not reach.
6. **Re-issue / versioning economics.** Bylaw change, address typo, deal pivot —
   new purchase, free fix, or discounted refresh? Unmodeled.
7. **Triage product is under-packaged.** "Flag when a P.Eng./surveyor/Site-Pro
   study is required and refer out" is the best **top-of-funnel** product (cheap,
   near-zero liability, routes into paid artifacts), not a guardrail footnote.
8. **Geographic TAM ceiling.** HRM-only; expansion = per-municipality re-ingest
   (the `phase-1-city-learning` work exists, but it's a real per-market cost).
9. **E&O insurance is gating, not optional** for transaction-facing artifacts —
   both a cost line and a marketable trust signal ("insured analysis").

## Guardrails (from the licensing analysis — apply from day one of artifacts)

- Never represent output as a Licensed Professional Planner's work; never use
  "LPP"/"MCIP" (the one bright statutory line, NS Professional Planners Act s.31).
- Build referral-trigger logic (P.Eng. / NS Land Surveyor / Site Professional)
  — protects users and is itself the triage product.
- Carry disclaimers that reports are bylaw-analysis tools, not official municipal
  determinations (esp. vs. the $200 Zoning Confirmation Letter).

---

## Go-to-market plan — one ladder, two SKUs that reinforce each other

The connective tissue: **the conversation is the artifact's intake, and turn
spend is creditable toward an artifact.** A user explores in chat ("can I build
this deck?"), the model gathers the facts and detects a concrete need ("this
needs a variance"), and offers to produce the deliverable — pre-filled from the
conversation, with the turns already spent credited against the report price.
That makes the two SKUs a funnel, not a confusion: turns are how you *discover*
you need the artifact, the artifact is what you *buy*.

### Phase 0 — Conversations (now → launch). Motion: self-serve PLG.
Ship the turn-pack product per `2026-06-pricing-v2-turn-packs.md`
(ABS-305…310). **ABS-305 (cumulative per-turn breaker) is doubly load-bearing
here** — it's also the cost-bounding primitive the artifact composition layer
will reuse. Instrument *what people ask about*: the topic distribution is the
prioritization signal for which artifact ships first.
- **Advance when:** turn packs are live, ABS-306 (Sonnet) verdict is in, and
  ≥~6–8 weeks of topic data exist.

### Phase 1 — Readiness/triage report (the bridge artifact). Motion: PLG, in-chat upsell.
Build the rendering + composition platform behind the *cheapest, lowest-liability*
artifact: a "what will my submission need?" readiness report (applicable
policies, triggered studies, referral flags). It proves the platform, carries
near-zero liability, and is the natural first in-conversation upsell. Price low
or bundle into larger turn packs as a hook.
- **Advance when:** platform renders/exports reliably; in-chat attach-rate to the
  readiness report is measurable (target a double-digit % of qualifying chats).

### Phase 2 — Variance package (first premium artifact). Motion: PLG + first channel tests.
The flagship: engine exists, highest volume, lowest discretion. Conversation
becomes the intake; turns credit toward the ~$200–400 price (anchored well under
the $800–$5,000 consultant fee). Begin realtor/lawyer referral experiments.
- **Advance when:** repeat-purchase or referral signal appears (the catalog's
  ~30%-repeat threshold for the transaction vertical is a reasonable trigger).

### Phase 3 — Low-discretion artifact line. Motion: channel-led (realtor/lawyer/lender).
Add zoning due-diligence memo, non-conforming-use analysis, as-of-right
compliance check (~2–3 weeks each on the built platform). This is the
transaction-due-diligence vertical; invest in the channel (brokerage/MLS
integration, referral partnerships) and stand up E&O insurance before scaling.
- **Advance when:** the due-diligence vertical shows recurring channel demand.

### Phase 4 — Consultant accelerator (discretionary files). Motion: B2B sales.
Sell first-draft rationales for rezoning / DA / MPS amendments to LPP/MCIP firms
and law offices as a *drafting accelerator*, not a standalone deliverable —
sidestepping the (waivable) MPS planner-authorship expectation and the bundled
stamped studies. Different buyer, different motion, highest value.

### Pricing interplay (avoid cannibalization)
- Turns: $5–8/turn packs (pricing-v2). Artifacts: $150–500, consultant-anchored.
- **Turn→artifact credit** is the bridge: exploration spend is creditable toward
  the report, so the two SKUs reinforce rather than compete, and the customer
  sees a single coherent ladder (explore cheap → buy the deliverable).
- The motion *climbs the value ladder*: **PLG (turns/homeowners) → channel
  (artifacts/transactions) → B2B (accelerator/consultants).**

## Open decisions
1. ~~Turn→artifact credit mechanic: full credit, partial, or "artifact includes N
   follow-up turns"?~~ **Deferred (decided 2026-06-12).** Left open deliberately
   until Phase 2 is in view — the choice changes how the two SKUs net out
   financially and should be made against real turn-revenue and artifact-pricing
   data, not pre-committed. Nothing in Phase 0/1 depends on it. The credit bridge
   remains the intended *mechanism*; only its rate/form is unfixed.
2. Does the readiness report (Phase 1) get built standalone, or does ABS-307/308
   (turn billing) ship first and the platform come immediately after? (Sequencing
   of the same team.)
3. E&O insurance timing — before Phase 2 (first premium artifact) or before
   Phase 3 (transaction-facing scale)?
4. Geographic: stay HRM-deep through Phase 4, or start a second municipality once
   the artifact platform exists?

## Not yet scheduled
This is strategy. No Linear issues beyond the existing turn-pack set
(ABS-305…310) until a direction is chosen. The first artifact-line issue would be
the rendering/composition platform (Phase 1), gated on Phase 0 launch + topic data.
