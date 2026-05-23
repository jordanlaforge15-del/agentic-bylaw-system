# Canadian municipality pilot shortlist v1 (ABS-61)

A ranked shortlist of 8 Canadian municipalities to approach for the
first municipal pilot, with explicit ranking criteria so the choice is
defensible and re-runnable.

The goal of this document is not to pick "the" pilot — it is to make
the *order of outreach* defensible. Top 3 get pursued in parallel; the
rest are a queue if those don't convert in 6 weeks.

> **Note on data freshness.** Population, housing starts, and digital-
> portal descriptions in this document are taken from public sources
> as of 2026-05. Before any outreach email goes out, the candidate row
> for that municipality is re-verified — at minimum the chief-planner
> name, the bylaw URL, and the last housing-starts figure. The
> verification box at the bottom of each card tracks that.

## 1. Why now and why municipalities

The architect-side product (Phase-2) is the wedge: it reads an IFC,
extracts setbacks/coverage/FAR, and produces a compliance check
against parcel-level zoning data. A municipal pilot is the natural
next surface — the same engine that helps an architect self-check
can help a planner *triage* incoming applications and surface the
"why" of each rule alongside the geometry.

Picking the wrong municipality costs ~6 months: long procurement
cycles, dead-end pilots in towns that were never going to buy, or
worse, a pilot in a city already running a competitor's tool that we
only discover after the kickoff meeting. The cost of the wrong target
is asymmetric — ranking is cheap insurance.

## 2. Ranking criteria

| # | Criterion | Weight | What "high" looks like |
|---|---|---:|---|
| 1 | **Housing pressure** | 0.30 | Top-quartile housing-starts growth, public political pressure to speed approvals, Council motions on permit timelines. |
| 2 | **Digital-permitting readiness** | 0.25 | Public online permit portal, open GIS / parcel layer, evidence of past pilots with civic-tech tools. |
| 3 | **No incumbent AI tool** | 0.20 | No public mention of Archistar / CivCheck / Symbium / similar AI-assisted permit-review vendor under contract. |
| 4 | **Bylaw availability** | 0.15 | Modern PDF zoning bylaw (machine-readable), open parcel layer, last consolidation < 5 years old. |
| 5 | **Halifax adjacency** | 0.10 | Atlantic-Canada municipality where a Halifax architect-side proof point transfers cleanly to a planner pitch. |

Each criterion is scored 0–10. Total = `sum(score_i × weight_i)`,
max 10.0. Anything ≥ 6.5 is in the "pursue" tier; 5.0–6.4 is "queue";
< 5.0 is "skip."

### Score rubric per criterion

- **Housing pressure (10):** Top-decile housing-starts growth (>10% YoY)
  *and* council motion or mayoral directive on permit timelines on the
  public record. **(5):** Above-average growth, no public political
  mandate. **(0):** Flat or declining starts.
- **Digital-permitting readiness (10):** Public online portal accepting
  uploads, open parcel/zoning layer (GeoJSON or WMS), documented past
  pilot with a civic-tech vendor. **(5):** Portal exists but is form-
  intake only. **(0):** Paper-and-counter intake.
- **No incumbent (10):** No public AI-permit-review vendor anywhere in
  the municipality's procurement record. **(5):** Sister-municipality
  in the same region has piloted a competitor; risk of leakage.
  **(0):** Active vendor contract.
- **Bylaw availability (10):** Consolidated zoning bylaw available as a
  modern (text-extractable) PDF, parcel layer in open data portal,
  last consolidated within 2 years. **(5):** PDF available but
  scan-quality or > 5 years stale. **(0):** No public consolidated PDF.
- **Halifax adjacency (10):** Atlantic-Canada and same regional
  press / professional-network exposure as a Halifax architect pilot.
  **(5):** Same province as an existing reference customer but no
  obvious press overlap. **(0):** No transfer story.

## 3. Exclusions (hard filters before scoring)

The following municipalities are excluded from consideration; rationale
captured so the exclusion is auditable.

| Excluded | Reason |
|---|---|
| Surrey, BC | Archistar live (public reference, 2024). |
| Toronto, ON | RFP-driven procurement, 12–18 month sales cycle, council politics. Worth revisiting at Phase-4 with a reference customer in hand. |
| Montréal, QC | French civil-code bylaws + language layer in the parser; pilot scope creep. Worth a separate plan, not this one. |
| Vancouver, BC | Has internal data-science team building in-house tooling; signal from Phase-1 conversations is "we'll build it." |
| Calgary, AB | "Building Permit Innovation" program internal; same in-house-build risk as Vancouver. |
| Ottawa, ON | Federal-overlap politics; long procurement. |
| Any municipality < 25,000 pop. | Below the volume threshold where a permit-triage tool meaningfully changes a planner's day. Even if the bylaw fits, the budget won't. |

## 4. Shortlist (ranked)

The table below is the executive view; detailed cards follow.

| Rank | Municipality | Province | Pop. (approx) | Housing | Digital | No-incumbent | Bylaw | Halifax adj. | **Total** |
|---:|---|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | Halifax (HRM) | NS | 480,000 | 9 | 7 | 9 | 8 | 10 | **8.45** |
| 2 | Moncton | NB | 80,000 (165k CMA) | 9 | 6 | 10 | 6 | 9 | **8.00** |
| 3 | Guelph | ON | 145,000 | 8 | 9 | 9 | 8 | 0 | **7.65** |
| 4 | Saskatoon | SK | 280,000 | 7 | 9 | 10 | 8 | 0 | **7.55** |
| 5 | Kelowna | BC | 145,000 | 10 | 8 | 7 | 7 | 0 | **7.45** |
| 6 | Charlottetown | PE | 40,000 | 8 | 5 | 10 | 6 | 9 | **7.45** |
| 7 | Victoria | BC | 95,000 (400k CRD) | 9 | 7 | 7 | 7 | 0 | **6.90** |
| 8 | Kingston | ON | 135,000 | 7 | 7 | 9 | 8 | 0 | **6.85** |

> Rank is strict descending by total. Where totals tie (Kelowna and
> Charlottetown both score 7.45), Kelowna is ranked higher because the
> *recommended pursuit order* in §6 picks it up earlier on time-decay
> grounds — see §6 for the qualitative reasoning. The raw scores are
> not perturbed by that judgement.

### 4.1 Halifax (HRM), NS — score 8.45 — **rank 1**

- **Population:** ~480,000 (HRM, 2024 estimate).
- **Housing starts growth:** ~8–10% YoY (2023–24), one of the fastest-
  growing CMAs in Canada. CMHC: HRM is the fastest-growing CMA outside
  the GTA / Vancouver.
- **Permitting volume:** ~9,000 building-permit applications/year
  (HRM open data, 2023).
- **Current digital state:** HRM Permit Portal (online intake +
  status), open parcel & zoning layer on the open-data portal, public
  consolidated Land Use Bylaw + Regional Plan.
- **Known AI/automation activity:** None public. HRM has piloted civic-
  tech (transit, 311) but not permit AI.
- **Bylaw accessibility:** Consolidated LUB PDF + Regional Centre
  Secondary Municipal Planning Strategy, both modern text-extractable
  PDFs, hosted on `halifax.ca`.
- **Champion candidates:** Chief Planner; Director of Planning &
  Development; the architect we are running the Phase-2 pilot with
  acts as a *de facto* referral. *Specific names verified before
  outreach.*
- **Score per criterion:**
  - Housing 9 · Digital 7 · No-incumbent 9 · Bylaw 8 · Adjacency 10 → 8.45
- **Recommended outreach:** Warm intro via Phase-2 architect pilot
  *after* one project clears the pilot scorecard. Don't pitch HRM
  before we have a Halifax architect reference. **Trigger:**
  Phase-2 project 1 hits scorecard thresholds.
- **Verification before outreach:** [ ] Permit volume figure;
  [ ] Chief planner name; [ ] LUB last-consolidated date;
  [ ] Phase-2 architect agrees to the introduction.

### 4.2 Moncton, NB — score 8.00 — **rank 2**

- **Population:** ~80,000 (city), ~165,000 (Greater Moncton CMA).
- **Housing starts growth:** Among the top in Atlantic Canada;
  Greater Moncton CMA had ~10–15% YoY housing starts growth in
  2023–24 driven by interprovincial migration.
- **Permitting volume:** ~2,500 building-permit applications/year
  (Greater Moncton, rough estimate from city annual reports).
- **Current digital state:** Online application portal for permits;
  parcel data via GeoNB (provincial layer); city does not yet expose a
  zoning WMS but the PDFs are clean.
- **Known AI/automation activity:** None known.
- **Bylaw accessibility:** Zoning By-law Z-213 available as a modern
  PDF on `moncton.ca`. Last consolidation 2023.
- **Champion candidates:** Director of Urban Planning; CAO. New
  Brunswick provincial housing strategy provides political tailwind.
- **Score per criterion:**
  - Housing 9 · Digital 6 · No-incumbent 10 · Bylaw 6 · Adjacency 9 → 8.00
- **Recommended outreach:** Cold email + LinkedIn to Director of
  Urban Planning, framed as Atlantic-region pilot building on Halifax
  proof points. Run *in parallel* with Halifax — Moncton doesn't
  depend on the Halifax architect pilot landing first.
- **Verification before outreach:** [ ] Director name + current title;
  [ ] Z-213 last consolidation; [ ] GeoNB parcel coverage for Moncton;
  [ ] Permit volume figure (currently rough estimate).

### 4.3 Guelph, ON — score 7.65 — **rank 3**

- **Population:** ~145,000.
- **Housing starts growth:** Moderate (~5–8% YoY). Bill 23 ("More
  Homes Built Faster Act," ON 2022) puts statutory pressure on
  permit timelines for all Ontario municipalities — Guelph's Council
  has signalled prioritization.
- **Permitting volume:** ~3,500 building-permit applications/year.
- **Current digital state:** Online portal (AMANDA-based) for permit
  applications, open data portal includes parcel + zoning layers as
  WMS / GeoJSON. Past civic-tech pilots (smart-city / IoT) on the
  public record.
- **Known AI/automation activity:** None for permit review;
  Guelph has been a Smart Cities Challenge participant which
  signals receptiveness to vendor pilots.
- **Bylaw accessibility:** Zoning By-law 1995-14864 + amendments,
  available as a modern consolidated PDF, last consolidated 2024.
- **Champion candidates:** General Manager, Planning & Building
  Services; Chief Building Official.
- **Score per criterion:**
  - Housing 8 · Digital 9 · No-incumbent 9 · Bylaw 8 · Adjacency 0 → 7.65
- **Recommended outreach:** Cold email to GM Planning & Building,
  citing Bill 23 + a brief Halifax-architect reference. Guelph's
  prior smart-city posture makes them more receptive to vendor
  pilots than the mid-Ontario average.
- **Verification before outreach:** [ ] GM Planning name;
  [ ] Bylaw consolidation date; [ ] Confirm AMANDA-based portal;
  [ ] Confirm zoning WMS endpoint is public.

### 4.4 Saskatoon, SK — score 7.55 — **rank 4**

- **Population:** ~280,000.
- **Housing starts growth:** ~5–7% YoY. Moderate, not top-quartile.
- **Permitting volume:** ~4,500 building-permit applications/year.
- **Current digital state:** ePermitting portal; Saskatoon is a
  long-time open-data leader (one of the first Canadian cities with a
  formal open-data program, 2013). Open zoning and parcel layers.
- **Known AI/automation activity:** None known.
- **Bylaw accessibility:** Zoning Bylaw No. 8770 + amendments,
  consolidated 2023, modern PDF, open data portal.
- **Champion candidates:** Director of Planning & Development;
  General Manager, Community Services.
- **Score per criterion:**
  - Housing 7 · Digital 9 · No-incumbent 10 · Bylaw 8 · Adjacency 0 → 7.55
- **Recommended outreach:** Cold email; lead with the open-data
  story ("you've already published the layers — we just close the
  loop"). Lower urgency than the top 4; queue for week 4–6 if the
  others stall.
- **Verification before outreach:** [ ] Director name;
  [ ] ePermitting portal vendor; [ ] Bylaw 8770 consolidation date.

### 4.5 Kelowna, BC — score 7.45 — **rank 5 (pursue parallel, see §6)**

- **Population:** ~145,000 (city), ~225,000 (Central Okanagan).
- **Housing starts growth:** Top-decile nationally. Kelowna has been
  one of the highest-pressure housing markets in Canada since 2021,
  with sustained Council attention on permit-process reform.
- **Permitting volume:** ~4,000 building-permit applications/year
  (city annual report, 2023).
- **Current digital state:** ePermitting portal (live since 2022);
  open data portal with parcel and zoning; documented past process-
  improvement initiative ("Building Permit Process Review," 2023).
- **Known AI/automation activity:** No public AI vendor.
  *Risk:* BC has the closest provincial-government attention to
  permit-AI in Canada (Surrey/Archistar story); a BC neighbour might
  poach the pitch. Hence "no-incumbent" score is 7, not 10.
- **Bylaw accessibility:** Zoning Bylaw No. 12375 (consolidated 2024,
  modern PDF); open parcel layer.
- **Champion candidates:** Divisional Director, Planning &
  Development Services; Chief Building Inspector.
- **Score per criterion:**
  - Housing 10 · Digital 8 · No-incumbent 7 · Bylaw 7 · Adjacency 0 → 7.45
- **Recommended outreach:** Cold email, with the housing-pressure
  angle ("here's the geometry-level check that compresses your
  setback-review step from days to minutes"). Move fast — Kelowna is
  the most likely municipality in this list to *also* be on
  Archistar's outreach radar.
- **Verification before outreach:** [ ] Director name;
  [ ] ePermitting vendor (in case Archistar partnership);
  [ ] Bylaw 12375 last amendment date.

### 4.6 Charlottetown, PEI — score 7.45 — **rank 6**

- **Population:** ~40,000.
- **Housing starts growth:** PEI is the fastest-growing province per
  capita (2022–24). Charlottetown specifically has run a multi-year
  housing-pressure narrative.
- **Permitting volume:** ~800 building-permit applications/year.
  Low absolute volume; *but* a low-volume municipality also means a
  short path to "the planner has time to talk to us."
- **Current digital state:** Online application form (PDF intake);
  parcel data via PEI provincial open data. No live ePermitting
  portal. Hence digital score 5.
- **Known AI/automation activity:** None.
- **Bylaw accessibility:** Zoning & Development Bylaw available as a
  modern PDF, last consolidated 2022.
- **Champion candidates:** Director of Planning & Heritage; Mayor's
  office (Charlottetown has historically been mayor-led on innovation
  pilots).
- **Score per criterion:**
  - Housing 8 · Digital 5 · No-incumbent 10 · Bylaw 6 · Adjacency 9 → 7.45
- **Recommended outreach:** Warm intro via Halifax / Atlantic
  professional network if available; otherwise cold email to
  Director of Planning. Charlottetown is small enough that the pilot
  can land in days, not months — useful as a *fast-feedback* pilot
  even if the absolute permit volume is small.
- **Verification before outreach:** [ ] Director name;
  [ ] Bylaw consolidation date; [ ] Whether a champion exists at
  mayor's-office level.

### 4.7 Victoria, BC — score 6.90 — **rank 7**

- **Population:** ~95,000 (city), ~400,000 (Capital Regional District).
- **Housing starts growth:** ~6–8% YoY in CRD. Provincial housing-
  pressure mandate via BC's `Housing Statutes (Transit-Oriented Areas)
  Amendment Act` (2023) puts top-down pressure on permit reform.
- **Permitting volume:** ~2,500 building-permit applications/year
  (city of Victoria); CRD totals much higher.
- **Current digital state:** ePermitting portal; open CRD GIS layers;
  capital-region cooperation means a pilot in Victoria can rapidly
  expand to Saanich, Oak Bay, etc.
- **Known AI/automation activity:** None public. *Risk:* same BC
  proximity to Surrey/Archistar caveat as Kelowna.
- **Bylaw accessibility:** Zoning Regulation Bylaw, consolidated 2024,
  modern PDF.
- **Champion candidates:** Director of Sustainable Planning &
  Community Development; CRD planning leads (regional play).
- **Score per criterion:**
  - Housing 9 · Digital 7 · No-incumbent 7 · Bylaw 7 · Adjacency 0 → 6.90
- **Recommended outreach:** Cold email; lead with the CRD regional-
  expansion angle ("one pilot, eight municipalities downstream"). The
  regional angle is the differentiator vs. a lone-city pilot, and
  CRD's pre-existing data-sharing makes it credible.
- **Verification before outreach:** [ ] Director name;
  [ ] Whether CRD has a formal multi-municipality pilot framework;
  [ ] BC TOA Act implementation status in Victoria.

### 4.8 Kingston, ON — score 6.85 — **rank 8**

- **Population:** ~135,000.
- **Housing starts growth:** ~4–6% YoY. Moderate. Bill 23 applies.
- **Permitting volume:** ~3,000 building-permit applications/year.
- **Current digital state:** Online permit portal; open data includes
  parcel and zoning; less aggressive on civic-tech pilots than Guelph.
- **Known AI/automation activity:** None known.
- **Bylaw accessibility:** Zoning By-law 2022-62, consolidated 2024,
  modern PDF.
- **Champion candidates:** Commissioner, Community Services;
  Director, Planning Services.
- **Score per criterion:**
  - Housing 7 · Digital 7 · No-incumbent 9 · Bylaw 8 · Adjacency 0 → 6.85
- **Recommended outreach:** Cold email after the top 4 have either
  converted or stalled at week 4. Kingston is stable-mid; not a
  fast-mover but a likely-buyer-eventually.
- **Verification before outreach:** [ ] Commissioner / Director name;
  [ ] By-law 2022-62 latest amendment.

## 5. Long-list considered (not shortlisted) — why

For audit / future-revisit purposes. Each was scored, fell below the
6.5 cutoff *or* lost on a qualitative tie-breaker.

| Municipality | Province | Rough total | Why not shortlisted |
|---|---|---:|---|
| Fredericton | NB | 6.10 | Smaller and slower-growing than Moncton; Moncton dominates the Atlantic adjacency play. |
| Saint John | NB | 5.40 | Lower digital readiness; reactive rather than reform-minded. |
| St. John's | NL | 5.20 | Flatter housing-starts curve; further from Halifax referral network than NB cities. |
| Lethbridge | AB | 5.80 | Modest housing pressure; AB provincial procurement climate uncertain. |
| Burnaby | BC | 6.40 | High volume, but procurement-heavy and proximate to Surrey/Archistar. |
| Coquitlam | BC | 6.30 | Same Tri-Cities / BC overlap risk; lower urgency than Kelowna or Victoria. |
| Hamilton | ON | 6.40 | Has had public friction with vendor pilots; political risk. Revisit in Phase-4. |
| Brampton | ON | 6.20 | High growth, but political churn at council level; sales-cycle risk. |
| Barrie | ON | 6.20 | Strong housing pressure but unclear digital readiness; needs more research. |
| Burlington | ON | 6.00 | Mid-growth, mid-digital; nothing stands out. |
| Windsor | ON | 5.50 | Flatter housing market; less urgency. |
| Regina | SK | 5.90 | Saskatoon dominates the Saskatchewan slot on digital readiness. |

## 6. Recommendation

Pursue the top 3 (by raw score) *in parallel*, not in sequence —
plus Kelowna as a time-sensitive strategic add:

1. **Halifax (HRM), NS** *(rank 1, 8.45)* — *primary, conditional on
   Phase-2 architect reference landing.* Warm intro via the architect
   pilot. Highest downside-protected play because the architect pilot
   creates the referral.
2. **Moncton, NB** *(rank 2, 8.00)* — *Atlantic-adjacency cold
   outreach.* Strong housing-pressure narrative, no incumbent risk,
   and the Halifax adjacency lets us reuse the same architect-pilot
   reference. Cold email + LinkedIn to Director of Urban Planning
   this week. Does not depend on Halifax landing first.
3. **Guelph, ON** *(rank 3, 7.65)* — *cleanest Ontario cold-outreach
   play.* Best digital readiness in the list, prior smart-city
   posture, no Halifax dependency. Cold outreach in parallel.

**Strategic add — Kelowna, BC** *(rank 5, 7.45)*. Despite the lower
raw score, Kelowna has the highest *time decay* in the list. BC is
the closest market to existing AI-permit-review vendors (Surrey /
Archistar), and Kelowna's combination of top-decile housing pressure
+ no incumbent yet makes it the municipality most likely to be on a
competitor's radar this quarter. Cold email within 2 weeks. If we
wait until Halifax/Moncton/Guelph stall, the Kelowna window may
already be closed.

If none of the top 3 (or Kelowna) has a kickoff call by week 6, fall
through to Saskatoon (rank 4 by score, but lower urgency) in week 7,
then Charlottetown, Victoria, and Kingston in that order. Note:
Charlottetown is the *fastest* pilot to actually run if a champion
materializes — keep it as the "wildcard fast-feedback" option even
out of order if a top-tier candidate signals interest but a slow
timeline.

### Outreach approach summary

Ordered by *recommended pursuit order* (not raw rank — Kelowna is
promoted on time-decay grounds, see above).

| Pursuit order | Rank | Municipality | Approach | Trigger |
|---:|---:|---|---|---|
| 1 | 1 | Halifax (HRM) | Warm intro via Phase-2 architect | Phase-2 project 1 passes scorecard |
| 2 | 2 | Moncton | Cold email — Atlantic regional angle | This week (parallel to Halifax) |
| 3 | 3 | Guelph | Cold email — Bill 23 + smart-city precedent | This week |
| 4 | 5 | Kelowna | Cold email — housing-pressure angle (time-decay) | Within 2 weeks |
| 5 | 4 | Saskatoon | Cold email — open-data closure angle | Week 4 if top tier stalls |
| 6 | 6 | Charlottetown | Warm intro via Atlantic network | Wildcard / fast-feedback option |
| 7 | 7 | Victoria | Cold email — CRD regional-expansion angle | Week 4 if top tier stalls |
| 8 | 8 | Kingston | Cold email — Bill 23 angle | Week 4 if top tier stalls |

## 7. What this document is *not*

- Not the outreach copy. Drafting the actual email templates is a
  separate issue once we have a champion name per row.
- Not a market sizing. The TAM question (Canadian municipalities
  with > 25k pop. = ~125 municipalities) is a separate exercise.
- Not a competitive scan. We exclude Surrey / Austin / Honolulu /
  Bellevue / Louisville as known incumbent cities, but a formal
  competitive map (Archistar, CivCheck, Symbium, UpCodes-for-zoning,
  etc.) belongs in a separate doc.
- Not a procurement playbook. Each Canadian municipality has its own
  procurement-threshold rules (sole-source caps, RFP triggers).
  Phase-1 pilots aim to fit *under* every municipality's sole-source
  threshold so we avoid the RFP path until a reference exists.

## 8. Update protocol

This is `v1`. Re-score on the following triggers:

- A municipality on the shortlist signs (or formally declines) a
  pilot → move it to `municipality_pilots_v1.md` (separate document,
  not yet created).
- A competitor announces a Canadian customer → re-score the
  "no-incumbent" column for everyone in their province.
- Phase-2 architect pilot signs / fails → re-weight Halifax adjacency
  (likely up if signed, drop to 0 if it fails).
- New municipality crosses the 25k population floor with a notable
  housing-pressure story → add to long-list, score, see if it makes
  the cut.

Bump the filename to `v2.md` on any re-score that changes the top 3.
