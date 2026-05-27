# ADR: Revit Plugin Strategy — Speckle vs Native Add-in

**Date:** May 26, 2026  
**Status:** Accepted  
**Issue:** ABS-60  
**Deciders:** Architecture, Product  

## Problem Statement

The bylaw advisor's Phase 2 integration delivers Revit data via Speckle, the open-source collaboration platform. Phase 3 is now entering pilot use. Before committing 6+ months to a native Revit add-in (a .NET project requiring Autodesk App Store listing, Windows-only distribution, and ongoing maintenance), we need real signal on whether Speckle meets user needs or if a native button inside Revit is essential.

## Decision Inputs

### 1. Pilot Customer Feedback

**Current state:** Phase 3 pilots are now live with Speckle as the data bridge.

**Question:** Are customers using it, or are they asking for a native Revit integration?

**Signal:** If pilot feedback shows customers are (a) regularly publishing to Speckle from Revit and (b) not complaining about the extra click/dependency, Speckle is viable. If feedback shows they're ignoring Speckle or saying "we'd use this if it were inside Revit," the native add-in becomes business-critical.

**Outcome at Phase 3 close:** Quantified usage metrics (% of pilot users sending data via Speckle) and at least 3 qualitative interviews with decision-makers (architects, BIM managers, IT) on Speckle friction.

### 2. Cost-of-Speckle to the Customer

**Vendor dependency risk:** Speckle is open-source-core, but production use typically runs against Speckle's hosted SaaS.

**Friction points:**
- Speckle account setup (new vendor, new credentials, new UI)
- SaaS cost (~$5k–$20k/year for teams) — adds to bylaw advisor TCO
- Outages or vendor changes cascade into our workflow
- Some enterprises (Fortune 500 BIM shops) have zero-third-party-SaaS policies
- Data residency: Speckle SaaS may not meet geo-compliance needs

**Mitigation:** Speckle self-hosted is an option, but pushes operational burden to customer. Not viable for SMB pilots.

**Outcome:** Phase 3 pilots must track which customers would self-host vs use SaaS, and whether Speckle cost is a deal-blocker.

### 3. Distribution & Ecosystem

**Speckle path:**
- Speckle Automate registry: small, growing, but not the canonical plugin discovery channel for architects
- Requires users to find & install Speckle Revit connector, then configure bylaw advisor integration
- Lower friction for tech-forward shops; higher friction for traditional AEC firms

**Native Revit add-in path:**
- Autodesk App Store: the gold standard for AEC plugin distribution
- Single installer, no external dependencies, native Revit UX
- Windows-only (Revit Mac exists but is limited; Mac adoption in AEC is ~15%)
- Requires .NET expertise, App Store vetting, ongoing maintenance
- Timeline: 4–6 months to first version; annual App Store listing fees (~$500)

**Hybrid path:**
- Ship both: Speckle for tech-forward shops, native add-in for traditional buyers
- Doubles maintenance burden (plugin code + Speckle integration logic)
- Justified only if both channels unlock distinct customer segments

## Decision

**→ Both, but Speckle first: Phase 3 proves the model; Phase 4 targets the native add-in**

### Rationale

1. **Phase 3 pilots are the test:** We need 2–3 months of real usage and customer feedback. Committing to a 6-month .NET build *before* we know if Speckle works is premature.

2. **Speckle is adequate if adoption is strong:** If 60%+ of Phase 3 pilots actively publish to Speckle and report no major friction, the native add-in ROI is low. We invest in Speckle marketplace presence and UX instead.

3. **Native add-in is justified if Speckle adoption stalls:** If customers say "we'd buy this if it lived inside Revit" or if self-hosted/cost barriers surface, the add-in becomes a Phase 4 priority.

4. **Ecosystem advantage:** By Phase 4, Revit 2025+ versions will be more stable, App Store policies may be clearer, and we'll have a mature Speckle integration to adapt into the .NET project. Starting the native build *during* Phase 3 pilots wastes momentum; starting it *after* Phase 3 insights is evidence-driven.

5. **Risk mitigation:** If we discover in Month 2 of Phase 3 that Speckle is dead-on-arrival, we have time to pivot to a native add-in build before Phase 4 planning. If we've already shipped a native add-in, we're carrying two distribution channels indefinitely.

## Verdict & Next Steps

### Decision: Speckle-first, native add-in as Phase 4 contingency

**Phase 3 actions (ongoing):**
- Pilot with Speckle Revit connector as the sole distribution method
- Collect usage metrics: how many users publish, how often, any churn
- Conduct 3–5 customer interviews on Speckle friction, cost objections, SaaS policy constraints
- Document decision signal: by end of Phase 3, we'll have quantified adoption and qualitative feedback

**Phase 4 decision gate (end of Phase 3):**
- **If Speckle adoption >60% and customer feedback is positive:** Invest in Speckle marketplace listing, Speckle Automate connectors, and improved Speckle UI
- **If Speckle adoption <40% or major friction surfaces:** File new issues for .NET add-in project, Autodesk App Store listing, and Windows distribution pipeline
- **If adoption is mixed (40–60%) or split by customer type:** Evaluate the hybrid approach; file Phase 4 epics for both tracks

**Owner:** Product + Architecture  
**Review date:** End of Phase 3 (approximately August 2026)

## Alternatives Considered

### Alt 1: Invest in native add-in immediately
**Rejected.** Commits 6 months and a new .NET team before we know if Speckle is good enough. High sunk cost if Phase 3 proves Speckle works; forces us to maintain both channels if it doesn't. Evidence-driven phasing is better.

### Alt 2: Speckle-only, indefinitely
**Rejected.** Real risk that traditional AEC shops (large firms, conservative buyers) will demand a native plugin. Autodesk App Store distribution is table-stakes in enterprise AEC. Deferring the decision indefinitely costs us market segment. Better to set Phase 4 gates now.

## Links & References

- [ABS-80: Phase 2 Speckle integration](https://linear.app/agenticbylawsystems/issue/ABS-80)
- [Speckle Revit Connector](https://speckle.systems/integrations/revit/)
- [Autodesk App Store policy](https://www.autodesk.com/developer/get-started/app-store)
- Speckle self-host costs: $500–$2k/month (not viable for SMB pilots)
- Phase 3 pilot customer list: [link to onboarding sheet]
