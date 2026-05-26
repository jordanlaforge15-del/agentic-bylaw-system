# Handoff: ABS° — Agentic Bylaw System (Marketing Site + Product Shell)

## Overview

**ABS°** ("abs" + a degree mark) is an agentic land-use bylaw assistant. The
agent reads the local Land Use By-law (HRM, in private beta), applies it to a
specific parcel, and returns a sourced, dated reference — verdict + reasoning +
exact citations.

This handoff covers the **marketing site + logged-in product shell** for ABS°.
It is one application with hash-based routing and six routes:

| Route       | Purpose                                                       |
| ----------- | ------------------------------------------------------------- |
| `#home`     | Landing page — hero, sample readings, CTA                     |
| `#pricing`  | Three tiers + FAQ                                             |
| `#login`    | Email + password (with magic link / Google) + last-session preview |
| `#signup`   | Invite request form, qualifying questionnaire → confirmation  |
| `#billing`  | Logged-in account screen — plan, payment, usage, invoices     |
| `#app`      | The product itself — three-pane chat with reasoning + parcel pane |

The site uses a **dual-theme system** — every screen renders in either light
("Blueprint") or dark ("Setback") mode. The mode toggle lives in the top nav
and persists via the in-design Tweaks panel. Both modes are first-class; pick
defaults but treat them as equally polished.

## About the Design Files

The files in `design_files/` are **design references created in HTML/JSX**.
They are prototypes showing intended look and behavior — **not production code
to copy directly**. They use React 18 via `<script>` tags + Babel Standalone in
the browser; this is fine for a design preview but is not how to ship.

The task is to **recreate these designs in the target codebase's existing
environment**, using its established patterns:

- If the target is Next.js / React + a CSS solution (Tailwind, vanilla-extract,
  CSS modules, etc.), reimplement with proper components and route files.
- If no target framework exists yet, choose **Next.js 15 + React 19 + Tailwind
  CSS** as the default — it's the closest fit for what's mocked here.

Treat the JSX files as a precise spec for layout, copy, color, type, and
interaction — not as an importable module.

## Fidelity

**High-fidelity.** Colors, typography, spacing, copy, and interactions are all
intended to be lifted exactly. The only intentional placeholders are:

- The site-plan SVG in the parcel pane (an abstract sketch, not a real survey)
- The agent walkthrough's three sample addresses (real HRM streets, fabricated readings)
- The "address demo" on the home page only resolves the three known sample addresses; any other input falls through to the first sample

## Design Tokens

### Colors

The two theme objects are defined in `design_files/theme.jsx`. Reproduce them
as CSS variables (or Tailwind theme extensions) keyed off `[data-mode]`.

#### Light · "Blueprint"

| Token        | Value                  | Use                                           |
| ------------ | ---------------------- | --------------------------------------------- |
| `surface`    | `#ffffff`              | Page background                               |
| `surfaceAlt` | `#f7f6f2`              | Cards, billing rows, signup right pane        |
| `surfaceInk` | `#0a0a0a`              | Inverted blocks (dark CTAs, footer-like cards) |
| `text`       | `#0a0a0a`              | Body text, headings                           |
| `textMuted`  | `#7a7468`              | Secondary text, mono captions                 |
| `rule`       | `rgba(10,10,10,0.45)`  | Strong horizontal rules                       |
| `hair`       | `rgba(10,10,10,0.10)`  | Hairline borders, dotted dividers             |
| `accent`     | `#c9f24c`              | Highlight bar, accent fills, focal CTAs       |
| `accentInk`  | `#5a7a1a`              | Accent text on light surface                  |
| `onAccent`   | `#0a0a0a`              | Text on `accent` fills                        |
| `brick`      | `#a64b2a`              | Tertiary accent (rarely used)                 |
| `overlay`    | `rgba(10,10,10,0.5)`   | Modal scrims                                  |

#### Dark · "Setback"

| Token        | Value                       | Use                                             |
| ------------ | --------------------------- | ----------------------------------------------- |
| `surface`    | `#0a0a0a`                   | Page background                                 |
| `surfaceAlt` | `#171614`                   | Cards, parcel pane, billing rows                |
| `surfaceInk` | `#ede8db`                   | Inverted blocks (light CTAs)                    |
| `text`       | `#ede8db`                   | Body text                                       |
| `textMuted`  | `#9a9484`                   | Secondary text                                  |
| `rule`       | `rgba(237,232,219,0.50)`    | Strong horizontal rules                         |
| `hair`       | `rgba(237,232,219,0.12)`    | Hairlines                                       |
| `accent`     | `#c9f24c`                   | Highlight (same as light)                       |
| `accentInk`  | `#c9f24c`                   | Accent text reads accent itself in dark         |
| `onAccent`   | `#0a0a0a`                   | Text on `accent` fills                          |
| `brick`      | `#d56a44`                   | Tertiary                                        |
| `overlay`    | `rgba(237,232,219,0.05)`    | Subtle inverted scrim                           |

### Typography

- **Sans / display:** **Inter Tight** — weights 300, 400, 500, 600, 700, 800
- **Mono / caption:** **JetBrains Mono** — weights 400, 500
- Both loaded via Google Fonts in production
- `::selection` background uses `#c9f24c` with `#0a0a0a` text in both modes

#### Display & headings

| Use                             | Family       | Size  | Weight | Letter-spacing | Line-height |
| ------------------------------- | ------------ | ----- | ------ | -------------- | ----------- |
| Hero H1 (`Read the bylaw…`)     | Inter Tight  | 80–96px | 800  | -0.045em       | 0.95        |
| Page H1 (Pricing, Billing)      | Inter Tight  | 56px  | 800    | -0.04em        | 1.0         |
| Section H2                      | Inter Tight  | 44px  | 800    | -0.035em       | 1.05        |
| Card title / step title         | Inter Tight  | 28px  | 700    | -0.025em       | 1.1         |
| Auth H1                         | Inter Tight  | 52px  | 800    | -0.04em        | 1.0         |
| Verdict in chat answer          | Inter Tight  | 24px  | 800    | -0.03em        | 1.1         |
| Reading verdict in samples      | Inter Tight  | 24–32px | 800  | -0.025em       | 1.15        |

#### Body

| Use                       | Family       | Size  | Weight | Line-height |
| ------------------------- | ------------ | ----- | ------ | ----------- |
| Lead paragraph            | Inter Tight  | 19px  | 400    | 1.45        |
| Standard body             | Inter Tight  | 14–15px | 400  | 1.5–1.6     |
| Small body / secondary    | Inter Tight  | 12.5–13.5px | 400 | 1.45–1.55 |

#### Mono captions

Used everywhere for kickers, labels, metadata. `letter-spacing: 0.14em–0.18em`,
`text-transform: uppercase`, sizes **9–11px**. Color is `textMuted` by default,
`text` when `muted={false}`, or `accentInk` when called out.

### Spacing & layout

- Page max-width: **1340px** (most sections), **1200px** (Pricing, Billing),
  **980px** (narrow text blocks)
- Standard section padding: **64px vertical, 36px horizontal**
- Hero section padding: **72px / 56px / 36px**
- Card padding: **22–32px** depending on density
- Top nav height: 16px vertical padding × 36px horizontal
- Footer: 40px top / 28px bottom × 36px sides, 80px top margin from content

### Borders & rules

- Hairline: `1px solid hair`
- Strong rule: `1px solid rule` (used on header, section dividers)
- Inverted CTA cards use `border: none` and contrast via `surfaceInk`
- Inputs: `1px solid text` (heavy) — intentional, gives a drafted/blueprint feel
- **No border-radius** anywhere. Sharp corners are part of the brand.

### Shadows

Effectively none. The brand reads as a printed reference — depth comes from
hairlines and inversion, not shadows.

## Logo

**Wordmark:** the literal text `abs` followed by a small filled rectangle the
height of the lowercase x-height, in the accent color (`#c9f24c`). See
`ABSLogo` in `theme.jsx`. The mark is the wordmark — there is no separate icon.

```
abs█      ← the █ is a vertical bar, ~18% width × 78% height of the type size
```

- `font-family: Inter Tight`
- `font-weight: 800`
- `letter-spacing: -0.06em`
- The bar sits to the right of `abs` with `margin-left: 8% of size`
- The bar is `18% wide × 78% tall` of the font size

When referred to in copy, it's spelled `ABS°` — the degree mark is editorial,
**not** part of the rendered logo.

## Routes / Screens

### `#home` — Landing

Stacked sections inside a 1340px container. Top to bottom:

1. **Hero** — two-column grid `1.05fr / 1fr`, 56px gap.
   - Left: kicker (`HALIFAX REGIONAL MUNICIPALITY · PRIVATE BETA · MAY 2026`),
     H1 `Read the bylaw <highlight>like an expert.</highlight>`, lead paragraph,
     two CTAs (`Request access →` accent, `See pricing` ghost), three
     stat-cards (HRM / 38,420 parcels / 0.94 confidence) divided by hairlines.
   - Right: **AgentReader** — animated card showing the agent rotating through
     three sample addresses. Live "READING" pill, query typing in, a reading
     phase, then a verdict reveals with citation chip + confidence chip +
     "VERIFIED 2026·05·06" pill (accent). Cycle every ~5s.
2. **How it works** — three-column grid (no gaps; single bordered block with
   hairline dividers). Each cell: number, indigo/accent rule, title, body.
3. **Try it** — split with H2 left ("Paste an HRM address. See what's permitted.")
   and **AddressDemo** right. Free-text input + three suggestion chips. Submitting
   any of the three known addresses runs a 4-step "thinking" sequence and resolves
   to a verdict card. Other input falls through to the first sample.
4. **Real readings** — six cards in a 3-col grid. The middle-top card is
   "featured" (filled with `text` color, content reverses to `surface`).
   Each card: kicker (address + zone), italic query, big verdict, citation.
5. **Closing CTA** — full-bleed inverted block with a 52px headline and two CTAs.

### `#pricing`

Header with kicker, H1 `Three tiers. <highlight>One agent.</highlight>`, lead.
Below: 3-column grid of tier cards (Drafter / Practice / Developer). Practice is
"recommended" — fully inverted (surfaceInk background), with a "RECOMMENDED" tab
at top-right. Each card has tier kicker, title, blurb, price block with thin
hairline below, feature list (each line: small `+` bullet in accent), CTA.

Below tiers: 2-col grid of FAQ cards (4 items) on `surfaceAlt`.

### `#login`

Two-column `1fr / 1fr`, full viewport-minus-nav height.

- **Left (form):** kicker, H1 `Welcome <highlight>back.</highlight>`, sub.
  Email field, password field, "Forgot password?" right-aligned, primary
  `Log in →` (accent), divider with `OR`, two ghost OAuth buttons (Google,
  magic link), tail link to signup.
- **Right (preview):** mono kicker, "FROM YOUR LAST SESSION" card showing
  `5184 MORRIS ST · ER-1` reading with verdict and metadata. Tail line:
  "Three readings in progress, two awaiting your review."

Submitting the form (any input) routes to `#app`.

### `#signup`

Same two-column shell.

- **Left:** kicker `REQUEST ACCESS · ABS°`, H1 `Tell us about your <highlight>project.</highlight>`,
  email / name / role select / project textarea / disclaimer / submit.
- **Right:** "WHO USES ABS°" — three personas (Architects, Homeowners,
  Developers) each with a status pill `ACTIVE` and a one-line blurb,
  separated by hairlines.

Submit → confirmation state replaces the form: an accent confirmation card
with a generated `#ABS-NNNN` order id, "What happens next" 3-step list on the
right.

### `#billing`

Logged-in account view. Header with kicker `ACCOUNT · BILLING`, H1 `Billing.`, lead.

1. **Plan + payment** — two-column grid `2fr / 1fr`.
   - Left: full-bleed inverted plan card. Title `Practice`, price detail, an
     `ACTIVE` pill, three stat columns (SEATS / READINGS · MAY / NEXT INVOICE),
     two ghost CTAs.
   - Right: payment method card on `surfaceAlt`. VISA chip (44×30 dark block,
     mono `VISA`), masked PAN, expiry, "Update card" ghost button, billing
     email below a hairline.
2. **Invoice history** — table with header row in `surfaceAlt`, columns:
   `INVOICE / DATE / DESCRIPTION / AMOUNT / STATUS / ` (last col is download).
   5 rows. STATUS column is mono accent text `PAID`. Last column is a `PDF ↓`
   text button.

### `#app` — Product shell

Full-viewport, **no top nav** — replaced by an app-specific bar. Three vertical
panes:

- **App header** (full width, 14px vert / 22px horiz, hairline bottom):
  - Left: ABS logo (size 22), hairline divider, mono `READING · 5184 MORRIS ST · ER-1`,
    accent dot.
  - Right: mono `VERIFIED 2026·05·06`, ghost button → Account.
- **Left pane** (280px, hairline right, `surface`):
  - "+ New reading" primary button, full-width.
  - `RECENT · 6` kicker.
  - Six thread items. The active one has `surfaceAlt` background and a 2px
    accent left border. Each shows: address (bold), title (muted), zone +
    timestamp in mono. Unread items show a 6×6 accent dot.
  - Footer row: 28×28 accent square avatar `HS`, name `Halifax Studio`,
    plan line `Practice · 4 seats`.
- **Center pane** (flex 1, `surface`): conversation thread.
  - **System messages** render as a centered hairline divider with mono text in the middle.
  - **User messages** right-aligned, `text`-colored bubble with `surface`-color text, max 76% width, padding 12/16.
  - **Agent messages** left-aligned, no bubble. Header row: `ABSLogo size=18`,
    `ABS° · AGENT` mono kicker, spacer, accent `94% CONF` mono. Body indented
    28px:
    - **Verdict** — 24px Inter Tight 800, -0.03em letter-spacing
    - **Body** paragraph in `textMuted`-equivalent body color
    - **Reasoning toggle** — ghost button with `▸/▾` glyph and `N REASONING STEPS`
      mono label. Expands an inline table (3-col grid `36px 76px 1fr`) of
      step-by-step citations
    - **Source chips** below — small bordered pill, accent square + `§ … — Title`
  - **Composer** at bottom: suggestion chips row, then a heavy-bordered
    textarea with an accent send button. Below: `ENTER TO SEND · SHIFT+ENTER FOR NEWLINE`
    left, `NOT LEGAL ADVICE · VERIFY WITH HRM PLANNING` right, both mono muted.
- **Right pane** (320px, hairline left, `surfaceAlt`): parcel context.
  - `PARCEL` kicker + `CHANGE` mono button.
  - Address block.
  - **Site plan SVG** — 4:3 box, hairline outline, dashed setback, dark filled
    main house, accent-filled "SUITE" rectangle, frontage dimension line at top.
    `SITE · 1:200` corner mono caption.
  - Six dotted-hairline metadata rows (Lot area / Frontage / Zoning / Existing units / Heritage / Transit zone).
  - `CITED THIS THREAD · 3` kicker, three citation cards.
  - Sticky bottom: primary `Export reading (PDF)`, ghost `Share with team`.

## Interactions & Behavior

### Navigation

- Hash-based routing with these ids: `home`, `pricing`, `app`, `login`,
  `signup`, `billing`. The router lives in `app.jsx`'s `<App>` component.
- `go(route)` updates state, sets `window.location.hash`, and scrolls to top.
- The browser back button works because changes go through `hashchange`.
- The `#app` route bypasses `TopNav` + `Footer` — it owns its own chrome.

### Theme toggle

- Top nav has a sun/moon glyph button next to the `Request access` CTA. It
  toggles between `mode: 'light'` (Blueprint) and `mode: 'dark'` (Setback).
- The mode is stored in the Tweaks state (the `<TweaksPanel>` in this
  prototype). In a real app, store it in `localStorage` under a key like
  `abs:theme` and apply it via `data-mode` on `<body>` or `<html>`.
- The CSS in the host HTML keys two body backgrounds off `body[data-mode]`
  to prevent FOUC.

### Animations

- **AgentReader (home hero card):**
  1. `q` phase — type the query in 32ms/char
  2. 320ms pause, then enter `reading` phase
  3. After 1.5s, enter `a` phase — verdict reveals with a 0.4s
     `translateY(4px) → 0` + opacity fade-in
  4. After 3s in `a`, advance to next sample (mod 3)
- **AddressDemo:** 4 thinking steps at 460ms each, progress bar advances; on
  done shows the verdict card; reset button returns to idle.
- **Buttons:** `transform: translateY(1px)` on mousedown for a press effect.
  Transition 80ms.
- **Chat reasoning toggle:** instant expand/collapse — no animation needed.

### State

| Component        | State                                                |
| ---------------- | ---------------------------------------------------- |
| App router       | `route: string` synced with `window.location.hash`   |
| Theme            | `mode: 'light' \| 'dark'`                            |
| AgentReader      | `idx`, `phase: 'q' \| 'reading' \| 'a'`, `typed`     |
| AddressDemo      | `val`, `state: 'idle' \| 'thinking' \| 'done'`, `reading`, `step` |
| Login form       | `email`, `pw`                                        |
| Signup form      | `data: { email, name, role, project }`, `submitted`  |
| App reasoning    | `open: { [msgIndex]: boolean }`                      |

### Form validation

The mocks accept any input. In production:

- Email: standard RFC validation
- Project textarea: minimum 10 chars before submit enables
- Login: validate against API; show inline error in `brick` color

### Responsive

The mocks are designed for **1280px+ viewports**. On a real implementation:

- Below 1024px the home hero stacks (left then right card)
- Below 768px the pricing grid stacks
- Below 768px auth screens stack (form first, side panel below — or hide side)
- The `#app` view should display as **desktop-only** for v1; surface a "use a
  larger screen" message below 1024px. The three-pane layout doesn't graceful-degrade.

## Sample Data (Real Copy)

The mocks include plausible HRM addresses and bylaw-section references. These
should **stay as fixtures during development** — they read better than lorem.
The full set is inlined in `home.jsx` (`SAMPLE_ADDRESSES`) and `app-screen.jsx`
(`SAMPLE_THREADS`, `SAMPLE_MESSAGES`). Lift them as `mock.ts` for the implementation.

> Note on accuracy: the section numbers (e.g. `§ 9.4.3 — Backyard Suites`) are
> approximations of the real HRM Land Use By-law. Before launch, a planner needs
> to verify each fixture against the current bylaw text. Treat this as visual
> fidelity, not legal accuracy.

## Assets

- **Fonts:** Inter Tight + JetBrains Mono via Google Fonts (already wired in `ABS Website.html`)
- **Logo:** rendered in code (`ABSLogo` in `theme.jsx`) — no SVG file
- **Icons:** the project uses no icon library; small marks (`→`, `+`, `▸`,
  `▾`, `↻`, `✓`) are typographic. The arrow box in citation chips is a 4×4 div
  with the accent color. Keep this approach in implementation — do **not**
  reach for Lucide / Heroicons / etc unless a specific element calls for it.
- **Site plan SVG:** authored inline in `app-screen.jsx`. Keep it inline so it
  inherits theme colors from CSS variables.

## Files in this bundle

```
design_handoff_abs_website/
├── README.md                              ← you are here
└── design_files/
    ├── ABS Website.html                   ← entry point — open this in a browser to see the live prototype
    ├── theme.jsx                          ← color tokens, ThemeProvider, ABSLogo, Btn, Mono, HighlightWord
    ├── chrome.jsx                         ← TopNav, Footer
    ├── home.jsx                           ← #home — hero, AgentReader, AddressDemo, sections
    ├── pages.jsx                          ← #pricing, #login, #signup, #billing
    ├── app-screen.jsx                     ← #app — three-pane chat product shell
    ├── app.jsx                            ← App root + hash router + Tweaks integration
    ├── tweaks-panel.jsx                   ← in-design Tweaks UI (NOT for production — strip in implementation)
    └── responsive/
        ├── ABS Responsive.html            ← canvas of every mobile + tablet screen
        ├── responsive.jsx                 ← all responsive variants
        └── design-canvas.jsx              ← canvas wrapper (design-time only)
```

To preview the source designs locally: serve `design_files/` over any static
HTTP server (Babel Standalone needs real HTTP, not `file://`) and open
`ABS Website.html`.

## Responsive (375px mobile, 768px tablet)

Mobile and tablet variants of every key screen are in `design_files/responsive/`.
Open `ABS Responsive.html` in a browser to see them side-by-side on a pan/zoom canvas.

### Breakpoints

| Width    | Target                  |
| -------- | ----------------------- |
| 375px    | iPhone-class mobile     |
| 768px    | iPad-class tablet       |
| 1024px+  | Desktop (existing mocks) |

### Marketing home

- **Mobile (375):** single column, 24px gutters. Hero stat-cards stack
  horizontally with smaller numbers. AgentReader compresses to a 280px-tall
  card. How-it-works becomes three vertical cards. Proof grid: 1 column.
  Closing CTA stacks copy above buttons.
- **Tablet (768):** hero remains stacked (copy above AgentReader, full-width).
  How-it-works: 2-column grid + 1 spanning. Proof grid: 2 columns.

### Auth pages (login / get-an-invite)

- **Mobile (375):** form first, side-panel becomes a collapsible "Why ABS°"
  card below. Inputs full-width, large primary CTA, OAuth buttons stack.
- **Tablet (768):** stays single-column but with more breathing room (form
  centered, max-width 520, side-panel collapsed by default with a "Show
  details" toggle).

### `/app` shell

The product shell has three responsive states each on mobile and tablet. The
canvas shows all six.

**Mobile (375) — three states:**

| State              | What's visible                                                  |
| ------------------ | --------------------------------------------------------------- |
| Thread only        | Chat fills the screen. App bar has hamburger (left) + map glyph (right) |
| Drawer open        | Sidebar slides in from the left, 88% screen width, overlay scrim |
| Parcel sheet open  | Parcel pane is a bottom sheet, 80% screen height, drag handle on top |

**Tablet (768) — three states:**

| State                          | What's visible                                          |
| ------------------------------ | ------------------------------------------------------- |
| Sidebar + thread, parcel closed | Two panes. Map glyph in app bar opens parcel as overlay |
| All three panes                | Two-pane (sidebar + thread) + parcel as right-side overlay (320 wide) |
| Thread + parcel, sidebar closed | Hamburger collapses sidebar; thread + parcel side-by-side |

### CX decisions (from your spec)

- **Hamburger location:** left edge of app bar (16px from edge). 28×28 hit
  area, three 18px stacked lines. Animates to an X (rotates ±45°) when drawer
  is open. 200ms ease-out.
- **Drawer:** slides in from the left at 88% viewport width on mobile, 320px
  fixed on tablet. Scrim is `overlay` token at 50% opacity.
- **Parcel trigger:** small map icon button at the **right** of the app bar
  (mirroring the hamburger). 28×28, simple isometric site-plan glyph. Opens
  bottom sheet on mobile, side-overlay on tablet.
- **Bottom sheet:** anchored to bottom, 80% screen height max, 16px top
  border-radius (the only place radius is used in the brand — this is a
  platform affordance), drag handle (32×3, `hair` color) centered above the
  header. Spring open 240ms, hard close on backdrop tap.
- **iOS keyboard:** the composer uses `position: sticky; bottom: 0` and
  `interactive-widget=resizes-content` so the visual viewport shrinks under
  the keyboard. The thread auto-scrolls to bottom on focus. Avoid `100vh` —
  use `100dvh` everywhere.
- **Gestures:**
  - Swipe up from bottom edge → open drawer (24px bottom-edge hit zone)
  - Swipe down on bottom sheet → dismiss
  - Use a small JS gesture library or a pointer-events pattern with velocity
    threshold (~0.3 px/ms). The mocks include a static glyph showing the
    affordance; production needs the real gesture handler.
- **Scroll preservation:** when toggling drawer / sheet, the thread's scroll
  position must persist. Don't unmount the thread — translate the drawer/sheet
  over it. The mocks compose this way (drawer is `position: fixed`, sheet is
  `position: fixed bottom`).
- **Routing:** the parcel pane does **not** get its own URL. Sheet/overlay
  open/close is component state only. Browser back is reserved for actual
  route navigation. This matches your decision.

### Tablet vs. desktop breakpoint

At ≥1024px the layout is the desktop three-pane. Between 768 and 1024 you
have a judgement call — the mocks treat 768–1023 as tablet (sidebar can
collapse) and ≥1024 as full desktop. Keep that exact cutoff.

## Implementation notes

- **Strip the Tweaks panel.** It's a design-time control — drop `tweaks-panel.jsx`
  and the Tweaks integration in `app.jsx` entirely.
- **Theme via CSS variables.** Translate the two `ABSTheme` objects into
  `:root[data-mode="light"]` / `:root[data-mode="dark"]` blocks. Avoid passing
  theme through React context in production — CSS vars are simpler and faster.
- **Auth shell.** The two-column auth layout is the same primitive on `#login`
  and `#signup`. Build it once as `<AuthLayout>` with `kicker / title / sub /
  form / side` props.
- **Section component.** Most marketing sections share `kicker / title /
  children`. Build as `<Section>` once.
- **Routes.** In Next.js, each `#route` becomes a route folder under `app/`.
  The hash-based navigation in the prototype is just to keep one HTML file —
  in production, prefer real routes.
- **The `app` route is its own layout.** It bypasses the marketing shell. In
  Next.js, give it a separate `app/(product)/app/layout.tsx`.
