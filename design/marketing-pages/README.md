# Handoff: ABS Marketing & Legal Pages

## Overview

This package adds six previously-404 pages to the ABS (Agentic Bylaw System) marketing site: **Changelog, Coverage, Support, About, Privacy, Terms.** All six are built against the existing Blueprint/Setback design system already established on the site (Home, Pricing, Auth, Billing).

## About the Design Files

The files in this bundle are **design references created in HTML** — interactive prototypes showing intended look and behavior, not production code to copy directly. The task is to **recreate these designs in the target codebase's existing environment** (Next.js, Remix, etc.) using its established patterns, routing, and component library.

The prototypes use inline React + Babel for fast iteration. In production you should:

- Convert the inline-`style` objects to your styling solution (CSS modules, Tailwind, vanilla-extract, etc.).
- Use the codebase's existing router (file-based or otherwise) in place of the hash routing in `app.jsx`.
- Extract reusable atoms (`Mono`, `Btn`, `HighlightWord`, `Page`, `PageHead`, `LegalShell`, etc.) into shared components if not already present.

## Fidelity

**High-fidelity.** Pixel-perfect mockups with final colors, typography, spacing, and interactions. The included HTML loads in the browser and is the source of truth — open `ABS Website.html` and navigate via the footer or via `#changelog`, `#coverage`, `#support`, `#about`, `#privacy`, `#terms`.

Both light (Blueprint) and dark (Setback) modes are supported on every page; toggle via the top-right `04 / 03` switch.

## Routes

| Route hash | File | Purpose |
|---|---|---|
| `#changelog` | `changelog-coverage-support.jsx` → `ChangelogPage` | Versioned release history |
| `#coverage` | `changelog-coverage-support.jsx` → `CoveragePage` | Indexed jurisdictions + roadmap |
| `#support` | `changelog-coverage-support.jsx` → `SupportPage` | Help center |
| `#about` | `about-legal.jsx` → `AboutPage` | Company / mission / team |
| `#privacy` | `about-legal.jsx` → `PrivacyPage` | Privacy policy |
| `#terms` | `about-legal.jsx` → `TermsPage` | Terms of use |

These should map to file routes (e.g. `app/changelog/page.tsx`) in production.

---

## Shared Chrome

Every page sits inside `PageShell` (from `chrome.jsx`) which provides:

1. **Sticky `TopNav`** — 14px vertical, 32px horizontal padding, `border-bottom: 1px solid t.hair`, `backdrop-filter: blur(8px)`. Contains: logo + "HRM · PRIVATE BETA" mono label / nav buttons / theme toggle + primary CTA.
2. **Page body** (each page is responsible for its own layout, max-width 1200px via the `Page` wrapper from `pages.jsx`).
3. **`Footer`** — 4 columns (Brand · Product · Account · Company), 1px hair separator, ~80px top margin. All link items navigate via `setRoute` prop.

`Page` wrapper:
```jsx
<div style={{ padding: '56px 32px', maxWidth: 1200, margin: '0 auto', minHeight: 'calc(100vh - 200px)' }}>
```

`PageHead` (used by Changelog, Coverage, Support, Legal pages):
- Mono kicker (10/11px caps, letter-spacing 0.14em, color `textMuted`)
- H1: 56px, `font-weight: 800`, `letter-spacing: -0.04em`, `line-height: 0.98`
- Subtitle: 17px, color `textMuted`, line-height 1.45, max-width 620px
- Bottom border `1px solid hair`, padding-bottom 28px, margin-bottom 40px

---

## 1. Changelog Page (`#changelog`)

### Layout
Two-column grid: `220px | 1fr`, gap 48px, `align-items: flex-start`.

- **Left rail** (sticky, `top: 92px`):
  - Mono header "VERSIONS · 6"
  - Vertical list of versions; each row is a flex row with `vX.Y.Z` left and `MM-DD` right. Active version (first) gets a 2px lime left border and 600 weight.
  - Border-left: 1px hair on the column.
  - Below the list, a `surfaceAlt` card with a "Subscribe" link.

- **Right column** (entries):
  - Each release is an `<article>` card.
  - **Latest** (pinned) variant: `surfaceAlt` bg, 1.5px solid text border, 28px padding, a lime "LATEST" badge in top-right corner.
  - Standard variant: transparent bg, 1px hair border, 22px / 24px padding.
  - **Header**: version mono + 32px title; right side has `RELEASED · YYYY-MM-DD` mono. Summary paragraph below.
  - **Change list**: grid `76px | 1fr`, gap 14px. Each row has a `TagPill` and the change text.
  - Closing block: dashed-border note about pre-0.5.0 history.

### `TagPill` component
Three variants, all 9px JetBrains Mono caps, letter-spacing 0.14em, 64px min-width, centered:
| Tag | Background | Foreground | Border |
|---|---|---|---|
| `NEW` | `accent` (#c9f24c) | `onAccent` (#0a0a0a) | `accent` |
| `IMPROVED` | transparent | `text` | `text` (1px solid) |
| `FIXED` | transparent | `textMuted` | `hair` (1px solid) |

### Data shape
```js
{
  v: '0.6.0', date: '2026-05-14', label: 'Reading 2.0',
  pinned: true, summary: '…',
  changes: [{ tag: 'NEW' | 'IMPROVED' | 'FIXED', text: '…' }, …]
}
```

---

## 2. Coverage Page (`#coverage`)

### Layout
Standard `Page` wrapper. Three stacked sections:

1. **Active jurisdiction hero card** (dark on light, light on dark — uses `t.text` bg with `t.surface` fg):
   - Padding 32/32/28; flex row with title block (56px display H2, mono province kicker) and a lime "FULLY INDEXED" badge.
   - Below: 4-column stat strip (`PARCELS / BYLAW DOCUMENTS / TOTAL FRAGMENTS / LAST SYNC`) — 28px stat number, 700 weight, 9.5px mono label above.
   - Top border on stat strip: `1px solid rgba(255,255,255,0.15)`.

2. **Bylaws table** (1px hair border):
   - Header row: 12/18 padding, `surfaceAlt` bg, columns `2.4fr 1.6fr 0.8fr 0.8fr 0.7fr 0.6fr`, gap 16px. Header labels in 9.5px mono caps.
   - Body rows: 14/18 padding, 1px hair bottom border, font-size 13.5. Document name 600 weight + 9px mono ID below. Version in `textMuted`. Pages/Fragments in JetBrains Mono. Spatial column shows ✓ YES (accent-ink) or — NO (muted). Status as `Mono accent` for CURRENT.

3. **Methodology + Roadmap** (2-column grid, `1fr 1.2fr`, gap 56px):
   - **Methodology** (left): 32px display title with `HighlightWord`, then 4 ordered steps. Each step: 50px mono number column + content (16px bold title + 13.5px muted description).
   - **Roadmap** (right): list of cards (`surfaceAlt` bg, 1px hair, 14/16 padding). Each row is `1.4fr 0.7fr 1fr` columns: city/province / ETA mono / stage tag + colored 8px square. Stage colors: INDEXING → accentInk, NEGOTIATING → brick, BACKLOG → muted (with 0.35 opacity on square).
   - Beneath roadmap: dark CTA card "Want ABS in your city?" with accent button.

---

## 3. Support Page (`#support`)

### Layout
Standard `Page` wrapper. Four sections:

1. **Search + status row** (grid `1.6fr 1fr`, gap 14px):
   - Search input: 1.5px solid text border, white bg. Left "SEARCH ›" mono label, full-bleed input, dark "Search →" submit button on right.
   - Status panel: `surfaceAlt` bg, 1px hair border. Pulsing 10px lime dot + "SYSTEM STATUS / All systems operational" stack + "247 ms · p95" mono on right.

2. **Categories grid** (2×2, gap 14px):
   - Each card: 1.5px solid text border, surface bg, 24/26 padding.
   - Header row: 26px category name + 13px sub on left; large 36px mono icon (◐ ◧ ◨ ◓) in accent-ink on right.
   - Article list below header (separated by hair border): each row 10px vertical padding with article title + → arrow.

3. **Popular articles + Contact panel** (grid `1fr 1fr`, gap 56px):
   - **Popular** (left): h3 "What people read first." then numbered list. Each row grid `40px 1fr auto` with mono number, content (15.5px title + tag/duration mono row), and → arrow. Top border 1px hair on each row.
   - **Contact** (right): `surfaceAlt` bg, 1px hair, 28px padding. h3 "Talk to a human." Three contact cards stacked (Email / In-app chat / Office hours), each with surface bg + hair border, label + value. Email card uses `<a href="mailto:hello@abs.app">`.

---

## 4. About Page (`#about`)

### Layout
Standard `Page` wrapper. Five sections:

1. **Mission statement**: 56px, 700 weight, -0.04em letter-spacing, line-height 1.02, max-width 1080px. Contains `<HighlightWord height={0.16}>` for "too slow to read".

2. **Principles** (2×2 grid, single 1px hair border around the whole grid, internal borders between cells):
   - Each cell: 32/30 padding.
   - Top row: `§ 01` mono accent kicker + 28px×4px lime swatch on right.
   - 26px 700 title, 14px muted description.

3. **Origin + Timeline** (grid `1.2fr 1fr`, gap 56px):
   - **Origin** (left): mono kicker / 36px h3 / 15px body paragraphs.
   - **Timeline** (right): 1px hair card. Header row "TIMELINE / 2024 — NOW". Body rows: `100px 1fr` grid, date mono left + 13.5px text right, 1px hair separators.

4. **Halifax "where we are" dark card** (full-width, `t.text` bg):
   - 40/36 padding, grid `1fr 1fr`, gap 36px.
   - **Left**: coordinates mono kicker / 48px "Built in Halifax." / muted body.
   - **Right**: stylized "harbour" graphic — a 4:2.2 aspect ratio card with two horizontal hairlines bracketing a bar chart of 20 vertical bars (heights from the array `[18,32,28,46,36,58,42,54,38,72,50,64,48,56,40,42,30,24,18,14]`). Every 5th bar is lime; others are `rgba(255,255,255,0.55)`. Mono labels top (HRM / 5,490 km²) and bottom (PENINSULA / DARTMOUTH / BEDFORD / SACKVILLE).

5. **Team grid** (4 columns, gap 14px): each member card has a 1:1 portrait placeholder (striped diagonal lines via `repeating-linear-gradient(45deg, hair 0 1px, transparent 1px 12px)`; first card is solid accent), then a 16/16 info block with name (16px 700), role (13px), past (12px muted), location (9px mono caps).

6. **Closing CTA** (1.5px solid text border, 28/32 padding, flex justify-between): "SAY HELLO / hello@abs.app · @abs.halifax" on left; Talk-to-us + Get-invite buttons on right.

---

## 5. Privacy & 6. Terms — `LegalShell`

Both pages share a single `LegalShell` component. The shell renders:

### Plain-English summary banner
Full-width lime (`accent`) card, 22/26 padding, 40px bottom margin. Grid `160px 1fr` with `IN PLAIN ENGLISH` mono on left and 17px summary on right.

### Two-column body — sidebar + sections
Grid `240px 1fr`, gap 56px, `align-items: flex-start`.

**Sidebar (sticky, `top: 92px`):**
- Top mini-card: "CONSOLIDATED" mono / consolidation date / version mono.
- "CONTENTS" mono header.
- Section links: each is a flex row with `§1.0` mono badge + section title. Active state has lime 2px left border + `surfaceAlt` bg + 600 weight + `text` color (vs `textMuted`).
- **Active section tracked via `scroll` listener** — finds the last section whose `getBoundingClientRect().top < 140`.
- "EXPORT / Download PDF" card at the bottom.

**Section body:**
- Each section's `scrollMarginTop: 96` so anchor clicks don't hide under the sticky nav.
- Header: `§ N.0` mono accent kicker (12px) + 28px 700 H2 title, 1px hair bottom border.
- Body content is an array of blocks:
  - `{ k: 'p', v: '…' }` — plain paragraph.
  - `{ k: 'ul', v: [...] }` — list rendered with `(a) (b) (c)` JetBrains Mono markers (12px muted) in a 24px column, gap 4px.
  - `{ k: 'note', v: '…' }` — `surfaceAlt` bg with a 2px brick-color left border, "NOTE" brick-color mono label above the text.
- Closing "END OF DOCUMENT" dashed-border block.

### Privacy sections (8): Scope · Information we collect · How we use it · Subprocessors · Data location & retention · Your rights · Children · Changes
### Terms sections (10): Acceptance · The service · Accounts & access · Not legal advice · Acceptable use · Payment & cancellation · Confidentiality · Liability · Governing law · Changes

---

## Design Tokens

These come from `theme.jsx`. Both modes must be supported.

### Light mode — "Blueprint"
| Token | Value |
|---|---|
| `surface` | `#ffffff` |
| `surfaceAlt` | `#f7f6f2` |
| `surfaceInk` | `#0a0a0a` |
| `text` | `#0a0a0a` |
| `textMuted` | `#7a7468` |
| `rule` | `rgba(10,10,10,0.45)` |
| `hair` | `rgba(10,10,10,0.1)` |
| `accent` | `#c9f24c` |
| `accentInk` | `#5a7a1a` |
| `onAccent` | `#0a0a0a` |
| `brick` | `#a64b2a` |

### Dark mode — "Setback"
| Token | Value |
|---|---|
| `surface` | `#0a0a0a` |
| `surfaceAlt` | `#171614` |
| `surfaceInk` | `#ede8db` |
| `text` | `#ede8db` |
| `textMuted` | `#9a9484` |
| `rule` | `rgba(237,232,219,0.5)` |
| `hair` | `rgba(237,232,219,0.12)` |
| `accent` | `#c9f24c` |
| `accentInk` | `#c9f24c` |
| `onAccent` | `#0a0a0a` |
| `brick` | `#d56a44` |

### Typography
- **Display & body**: Inter Tight (weights 300/400/500/600/700/800), via Google Fonts.
- **Mono**: JetBrains Mono (weights 400/500).
- Default body: 'Inter Tight', system-ui, sans-serif.
- Default `::selection`: `background: #c9f24c; color: #0a0a0a;`.

### Type scale (key sizes)
| Use | Size / weight / tracking |
|---|---|
| H1 (PageHead) | 56 / 800 / -0.04em / 0.98 |
| H1 mission | 56 / 700 / -0.04em / 1.02 |
| H2 section | 32 / 700 / -0.03em / 1.05 |
| H2 cards | 26–28 / 700 / -0.025em / 1.1 |
| Card title | 16 / 700 / -0.015em |
| Body | 13.5–15 / 400 / line-height 1.5–1.6 |
| Body small | 12.5–13 / textMuted |
| Mono kicker | 10 / `JetBrains Mono` / letter-spacing 0.14em / uppercase |
| Mono small | 9–9.5 / letter-spacing 0.06–0.12em / uppercase |

### Spacing scale (used throughout, in px)
4, 6, 8, 10, 12, 14, 16, 18, 22, 24, 26, 28, 32, 36, 40, 48, 56, 72.

Grids: gap 14px for tight component grids; 28/36px between feature blocks; 56px between page sections.

### Borders
- Hair: `1px solid hair`
- Strong: `1.5px solid text` (used for primary CTAs, search input, Drafter/Developer tier cards)
- Accent left border for active states: `2px solid accent`

### No border-radius
The system is consistently sharp-cornered. Do not introduce rounded corners unless extending the system deliberately.

---

## Interactions & Behavior

- **Routing**: hash-based in the prototype (`#changelog` etc.). In production, replace with the codebase's router.
- **Theme toggle**: top-right `04 / 03` button flips `body[data-mode]` and re-renders ThemeProvider. Light = "04 Blueprint", dark = "03 Setback".
- **Changelog**: each version anchor (`#changelog-v0.5.0`) is internally linkable from the left rail.
- **Coverage**: hover/focus states not specifically defined; rely on the codebase's defaults.
- **Support**: search input has no live behavior in the mock — submit is no-op; wire to your search index.
- **Legal pages**:
  - Sidebar TOC has scroll-spy (see `LegalShell`). Replace with intersection-observer in production.
  - Sidebar anchors use plain `<a href="#tm-3">`-style fragment navigation. `scrollMarginTop: 96` on each section so the sticky chrome doesn't cover the heading.
- **No animation** beyond the existing `abs-pulse-dot` keyframe used on system-status dot in Support.

## State Management

Per page:
- Changelog: none.
- Coverage: none.
- Support: local `q` (search text) — wire to your search.
- About: none.
- Legal: local `active` section id; updated via scroll listener. Replace with `IntersectionObserver` in production.

App-level state (already in `app.jsx`):
- `route` (string)
- `mode` ('light' | 'dark')
- `tweaks` (object, persisted via the parent's edit-mode protocol — internal tooling, not needed in prod)

## Assets

No images. All visuals are CSS:
- Repeating-stripe pattern (team portraits) via `repeating-linear-gradient`.
- Block-bar "harbour" graphic on About page is drawn with 20 plain `<div>`s.
- All icons are unicode glyphs (`◐ ◧ ◨ ◓ → ↓ ↻ ✓ ›` etc.) — replace with the codebase's icon set if appropriate.
- Logo is the `ABSLogo` component in `theme.jsx` — typographic, no SVG.

## Copy

All copy in the prototype is **placeholder content written in the brand's voice** — appropriate for the HRM Halifax bylaw context but should be reviewed by the team before launch. The Changelog version history, Coverage roadmap dates, Privacy/Terms specifics, and team names are all illustrative.

## Files

- `ABS Website.html` — entry point
- `theme.jsx` — design tokens, ThemeProvider, primitives (`ABSLogo`, `Btn`, `Mono`, `HighlightWord`)
- `chrome.jsx` — TopNav, Footer, PageShell
- `app.jsx` — root component, routing, theme + tweaks state
- `pages.jsx` — shared `Page` + `PageHead`, plus pre-existing Pricing/Login/Signup/Billing
- `changelog-coverage-support.jsx` — **NEW** — ChangelogPage, CoveragePage, SupportPage
- `about-legal.jsx` — **NEW** — AboutPage, LegalShell, PrivacyPage, TermsPage
- `home.jsx`, `citations.jsx`, `app-screen.jsx`, `tweaks-panel.jsx` — supporting files included for reference (Home + product surface)
