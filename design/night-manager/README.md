# Handoff: Night Manager Mission Console

## Overview

The Night Manager (NM) is a Python orchestrator that runs overnight on the operator's local Mac. It pulls "Triaged" Linear issues, plans parallel execution, and spawns multiple Claude Code agents — each in its own git worktree. When agents finish, the NM reviews their code, runs E2E tests, and merges passing work into `dev`.

Today it runs headless in tmux. This design delivers a **local-first web UI** ("Mission Console") that makes the run legible at a glance: what each agent is doing, how the plan is progressing, what failed and why, and how to launch or review a run.

The intended deployment is a Next.js app on a spare port on the operator's machine, sharing the existing `web/` codebase's conventions (Next.js + Tailwind + shadcn/ui).

---

## About the Design Files

The HTML/JSX files in this bundle are **design references** — a clickable prototype produced in this conversation to show the intended look, layout, and live behavior. They are not production code to copy directly. They are also not a real codebase — there is no real backend, no real polling, no real Linear integration. State is hardcoded in `data.jsx`.

The task is to **recreate these designs in the target codebase** (Next.js + Tailwind + shadcn/ui per the brief) using its existing patterns. Read the HTML for visual reference; read `data.jsx` for the data shapes you'll be rendering from the real `state.json`; treat everything else as a styling spec.

If you find a place where the prototype's structure differs from what's idiomatic in the host codebase, prefer the host codebase. The aesthetic system below is what matters.

---

## Fidelity

**High-fidelity.** The prototype is pixel-precise with final colors, typography, spacing, status semantics, and interaction patterns. Recreate the UI faithfully using the codebase's stack; do not redesign the visual language. Match the corner-tick panels, the KPI strip, the agent-card layout, the status pill grammar, and the dense data-table treatment.

---

## Aesthetic — Three Themes

The console ships with three coherent themes the operator can swap between live via the **Display Mode switch in the top bar** (between the run telemetry and the clocks). All three share the same layout grammar — only colors and fonts vary.

- **Apollo** — 1970s NASA console: amber `#ffb000` on warm black `#0c0a07`, IBM Plex Mono. Reads as the classic mission-control look.
- **Vanguard** — modern flight deck: cyan `#5fe1d3` on cold graphite `#07090c`, JetBrains Mono. Recommended **production default**.
- **Red October** — submarine command bridge: alarm red `#ff0033` on pure black `#000000`, JetBrains Mono. Carries an additional sparse-scanline + dark vignette overlay for tactical-display feel; all status colors are reds at varying lightness so the palette stays monochromatic.

The default in the prototype is Vanguard. The other two are not vanity options — they're alternate operating modes the operator can pick based on environment (Apollo for warm-lit rooms, Vanguard for daylight, Red October for night-shift or "incident" mode where the operator wants the screen to feel urgent). Build all three; let the operator pick.

The switch persists the selection. In production, store the choice in `localStorage` keyed by user so the operator returns to the same theme on the next run.

---

## Design Tokens

### Colors (Vanguard)

| Token | Hex | Usage |
|---|---|---|
| `--bg` | `#07090c` | App background |
| `--bg-1` | `#0c1015` | Panel surface |
| `--bg-2` | `#11161d` | Panel header / hover |
| `--bg-3` | `#181f29` | Active row / nested raised |
| `--line` | `#1d2733` | Default 1px border |
| `--line-2` | `#2b3a4b` | Stronger border (slot edges) |
| `--line-3` | `#3f536a` | Corner ticks (brightest) |
| `--text` | `#d8e4ee` | Primary text |
| `--text-dim` | `#87a1b8` | Secondary text |
| `--text-mute` | `#4f6678` | Tertiary / labels |
| `--primary` | `#5fe1d3` | Brand / live signal / numeric readouts (cyan) |
| `--primary-d` | `#b0f3eb` | Primary hover |
| `--ok` | `#69e08d` | Merged / nominal |
| `--warn` | `#ffcd5b` | Blocked / disk warnings |
| `--err` | `#ff5d7a` | Failed / errors |
| `--info` | `#62b8ff` | In-progress / tool calls / live |
| `--review` | `#ffa657` | Reviewing |
| `--queued` | `#4f6678` | Queued (matches text-mute by design) |

Glow tokens (subtle bloom on primary numerics):
- `--glow-soft: 0 0 10px rgba(95, 225, 211, 0.14)`
- `--glow-hard: 0 0 14px rgba(95, 225, 211, 0.4)`

Status → tone mapping (used by `StatusPill`, plan slots, dots):
```
queued      → queued
in_progress → info  (cyan-blue + pulse + spinner)
reviewing   → review (amber-orange)
merged      → ok (green)
failed      → err (pink-red)
blocked     → warn (yellow)
```

### Typography

- **Primary font:** `JetBrains Mono`, weights 300/400/500/600/700, from Google Fonts.
- **Fallback:** `ui-monospace, monospace`.
- **Sans:** none — the entire UI is monospace by design. Do not introduce a sans face anywhere.

Scale (default; Vanguard reads at standard sizes):

| Use | Size | Weight | Tracking | Notes |
|---|---|---|---|---|
| Body | 13px | 400 | 0.005em | Default panel body text |
| Panel header label | 11px | 400 | 0.12em | Uppercase, color `--text-dim` |
| Panel header id | 11px | 600 | 0.12em | Uppercase, color `--primary` |
| Up / micro label | 10px | 400 | 0.12em | Uppercase, `--text-mute` |
| KPI value | 28px | 500 | 0.02em | Tabular nums, may use glow |
| KPI label | 10px | 400 | 0.14em | Uppercase, `--text-mute` |
| Numeric readout (display) | 15–36px | 500 | 0.04em | Tabular nums, `--primary` + soft glow |
| Plan group label (G01) | 18px | 500 | 0.04em | `--primary` |
| Plan slot id (ABS-90) | 12px | 600 | 0.04em | `--text`, nowrap |
| Plan slot title | 11px | 400 | line-height 1.35 | Clamp to 3 lines |
| Agent card id | 13px | 600 | — | `--primary` |
| Log line | 12px | 400 | — | `--text-dim` body, colored prefixes |
| Pill | 10px | 400 | 0.1em | Uppercase |
| Nav item | 11px | 400 | 0.12em | Uppercase |

All numeric readouts use `font-variant-numeric: tabular-nums` so they don't jitter when ticking.

### Spacing & layout

- **Grid base:** 4px. Padding inside panels: 14px. Console padding: 14px 18px 20px.
- **Panel gap:** 12px between panels in any grid.
- **Borders:** 1px solid `--line` for panel chrome; 1px solid `--line-2` for inset elements (plan slots, segmented controls); 1px solid `--line-3` for the 8×8px corner ticks.
- **No border-radius.** Every corner is square. The corner-tick decoration replaces the soft-radius affordance.
- **Min console width:** 1280px. Below this the page horizontal-scrolls; mission consoles assume a real monitor.

### Alternate theme overrides

Only the values that differ from Vanguard. All structural tokens (`--grid`, `--tick`, `--pad`, borders, shadow specs) stay identical.

**Apollo** (amber on warm black):
```
--font-mono: "IBM Plex Mono"
--bg:        #0c0a07      --bg-1: #110d09   --bg-2: #1a140c   --bg-3: #251c10
--line:      #3a2c17      --line-2: #5a4321  --line-3: #7a5d2e
--text:      #f4d59a      --text-dim: #b4915a  --text-mute: #71583a
--primary:   #ffb000      --primary-d: #ffd061
--ok: #6fdc8c   --warn: #ffc83d   --err: #ff5b3b   --info: #4ec0ff   --review: #ff9433   --queued: #71583a
```

**Red October** (alarm red on pure black; status colors are all red variants):
```
--font-mono: "JetBrains Mono"  + sparse scanline + dark vignette overlay
--bg:        #000000      --bg-1: #0a0303   --bg-2: #150505   --bg-3: #220707
--line:      #3a0a0a      --line-2: #5a0e0e  --line-3: #8a1818
--text:      #ff5555      --text-dim: #c43838  --text-mute: #6a1414
--primary:   #ff0033      --primary-d: #ff4d6a
--ok: #c43838   --warn: #ff7733   --err: #ff0033   --info: #ff3355   --review: #ff8855   --queued: #4a1010
```

Red October's overlay (in addition to the global grid):
```css
body[data-theme="red_october"]::after {
  background:
    repeating-linear-gradient(to bottom,
      rgba(0,0,0,0) 0px, rgba(0,0,0,0) 3px,
      rgba(40,0,0,0.35) 4px, rgba(0,0,0,0) 5px),
    radial-gradient(ellipse at center, rgba(0,0,0,0) 50%, rgba(0,0,0,0.7) 100%);
  position: fixed; inset: 0; pointer-events: none;
  z-index: 999; mix-blend-mode: multiply;
}
```

### WebKit caveat

Switching themes by toggling `body[data-theme]` does not invalidate already-resolved `var()` values on existing elements in Safari/WebKit (chromium handles it correctly). After changing the attribute, force a reflow:

```js
document.body.setAttribute("data-theme", next);
document.body.style.display = "none";
// eslint-disable-next-line no-unused-expressions
document.body.offsetHeight;
document.body.style.display = "";
```

Without this, plan-slot borders and other `var()`-driven colors stay on the previous theme until the page reloads.

### Background grid

Faint 64×64px grid behind everything, made of orthogonal 1px lines in `--line` at 0.18 opacity:

```css
body::before {
  position: fixed; inset: 0;
  background-image:
    linear-gradient(var(--line) 1px, transparent 1px),
    linear-gradient(90deg, var(--line) 1px, transparent 1px);
  background-size: 64px 64px;
  opacity: 0.18;
  pointer-events: none;
}
```

This is a critical part of the aesthetic — do not omit.

### Glow

Numeric "displays" (the digital-readout class) carry `text-shadow: var(--glow-soft)` to bloom subtly on the cyan primary. The active nav indicator carries `box-shadow: var(--glow-hard)`. The primary button carries `--glow-hard`. Use sparingly — only on intentional readouts and primary signals, never on body text.

---

## Component System

Every panel in this UI follows the same grammar. Build these as reusable shadcn-style components.

### `<Panel>`
Bordered region with header strip and corner ticks. Required for every grouping.

- Outer: `border: 1px solid var(--line)`; `background: var(--bg-1)`.
- Corner ticks: 4 × 8×8px L-shapes drawn with `::before`/`::after`/`.tick-bl`/`.tick-br` pseudo elements at each corner, `1px solid var(--line-3)`.
- Header row (`.panel-h`): `border-bottom: 1px solid var(--line); background: var(--bg-2); padding: 8px 12px; font-size: 11px; letter-spacing: 0.12em; text-transform: uppercase`.
  - Header structure (left → right): `<id>` (cyan, 4-char alphanumeric like `PLN-01`, `OPS-02`) · `<title>` (uppercase white) · `<meta>` (right-aligned, muted).
- Body (`.panel-b`): default `padding: 14px`; pass `flush` to remove padding when the body is its own table/list.

### `<StatusPill status>`
Uppercase, bordered chip, 10px tracked text, 2px 8px padding, leading dot (or spinner ring when `in_progress`). Color from the status→tone mapping above. Used on plan slots, agent cards, and issue header.

### `<Dot tone pulse>`
8×8px round, `background: currentColor`, glow via `box-shadow: 0 0 8px currentColor`. Pass `pulse` for `in_progress` — applies a 1.4s ease-in-out opacity/scale loop.

### `<KPI label value unit tone hint>`
Cell in the top strip. Renders a 10px tracked uppercase label, a 28px tabular-nums value (optional `unit` suffix at 13px muted), and an optional hint line. Tone tints the value (`prim`, `ok`, `err`, `warn`). The whole strip is one row of 6 cells separated by vertical 1px dividers.

### `<NightManagerMark>`
A target-reticle glyph: 16×16 viewBox, outer ring + tick marks at N/S/E/W + filled center dot. Stroke = `currentColor`. Used in the top-left brand block with a 28×28 bordered box around it and soft glow.

### Plan slot
Card representing one issue inside an execution group row.

- Border color = status tone (`in_progress` → cyan-blue, `merged` → green, `failed` → red, `reviewing` → amber, `queued` → faint dashed `--line`).
- Top row: status dot · issue id (12px bold white) · status text (right-aligned, status-tinted, 10px tracked).
- Body: issue title (11px `--text-dim`, line-clamp 3).
- Foot: `attempt N` · web port (right-aligned).
- Empty slot (slack capacity in a group): dashed border, single `·` centered, opacity 0.55.

### Agent card (live)
Card on the dashboard showing one running agent. ~220px tall.

- Header (`--bg-3` background): pulse dot · issue id (cyan) · title (one-line truncate) · `<StatusPill>` right.
- Body:
  - 2-col meta grid: `elapsed` (cyan tabular display), `attempt`, `branch` (truncate), `ports`.
  - "Current tool call" sub-panel: small uppercase label, then `<tool-name in cyan-blue> · <target path in dim>`. Updates whenever the agent invokes a new tool.
  - Mini log: last 5 events, monospace 10.5px, fading to background at the bottom via a linear gradient overlay. Each line: `[+HH:MM:SS] <name> <args>`. A blinking cyan █ cursor on the last line.

### System log row
Three columns: `time (mono mute)` | `level (uppercase, color-coded)` | `message (--text-dim)`. Levels: info (cyan), ok (green), warn (amber), err (red). Rows separated by `border-bottom: 1px solid var(--line)`.

### Timeline (issue detail)
Vertical thread of events with:
- A vertical 1px line behind a column of 10px dots (line stops at the first/last dot edges).
- Each row: dot · time · text.
- Pending events: dot is hollow (1px dashed `--text-mute`, transparent fill), text in `--text-mute`.
- Active event (e.g. "Coding in progress"): dot gets the pulse animation and a soft glow in its tone color.

### Segmented control
1px bordered horizontal row of options, each option `padding: 8px 16px`, divider between options. Active option: `background: var(--primary); color: var(--bg)`. Used for `Model` selection.

### Toggle
38×20 1px-bordered track with a 14×14 thumb. Off: thumb is `--text-mute`. On: track gains a tinted background, thumb moves to right and turns `--primary` with soft glow.

### Button (`.btn`)
- Base: 1px border `--line-2`, `--bg-2` fill, 11px uppercase 0.12em tracked text, 7px 14px padding.
- Hover: border + text → `--primary`, gains soft glow.
- `--prim` variant: filled cyan, dark text, hard glow.
- `--ghost`: transparent fill, dimmer border.
- `--danger`: red border + text; hover fills red with white text.

### Tabular list (`.tbl`)
Plain `<table>` with `border-collapse: collapse`. Header row: 10px uppercase 0.12em tracked, `--text-mute`, on `--bg-2`, sticky-top. Body cells: 12px regular, with `border-bottom: 1px solid var(--line)`. Hover tints the row in `--bg-2`. Used in the Launch screen's Linear queue preview.

---

## Screens

### 1. Top Bar (global)

Fixed across the top of every screen. Three regions divided by 1px vertical lines.

- **Left (brand block):** 28×28 cyan-bordered logo box containing the reticle glyph (with soft glow) · stacked `NIGHT MANAGER / mission console · v3.2.0` labels.
- **Center (run telemetry):** horizontal row of nowrap stats, separated by visual breathing room (~22px gap). Each stat: small uppercase label + bold/cyan value.
  - `SYSTEM NOMINAL` with pulsing green dot
  - `RUN nm-20260523-2300` (cyan)
  - `STARTED 23:00:00`
  - `ELAPSED 03:47:38` (cyan tabular display, ticks every second)
  - `AGENTS 4 / 3` (cyan)
  - `OPERATOR @nm-ops` (right-aligned)
- **Right (clock):** two vertically-stacked clock cells separated by 1px line — `LOCAL TIME` and `UTC`. Each shows an 18px tabular cyan readout above a 9px tracked muted label, top-aligned label.

Between the run-telemetry stats and the clock cluster, a **DISPLAY MODE** cell with the 3-way theme switch (Apollo / Vanguard / Red October). The cell has its own 1px left divider matching the rest of the bar's segmentation.

The brand block and right region both have 1px vertical dividers from the center. Background `--bg-1`, bottom-bordered.

### 2. Nav Bar (global, below top bar)

Horizontal tab strip. Each tab:
- `<num>` 9px bordered numeral box · uppercase tab label
- Default: `--text-mute` text; hover: `--text` text, `--bg-2` background; active: `--primary` text, `--bg-2` background, 2px cyan glow bar across the bottom edge.

Four tabs: `01 DASHBOARD` · `02 ISSUE DETAIL` · `03 LAUNCH` · `04 REPORTS`.

Right side of the nav: live status chips — `<dot> N agents live` · `<dot> M/T merged` · `<dot> F failed` (only if F > 0). 11px uppercase, dot color matches status tone.

### 3. Dashboard (`01`)

The killer view. Three regions:

**A. KPI strip** (top, full width, 6 cells in one bordered row): `Elapsed` (cyan), `Agents Active N / max`, `Merged M / total`, `Failed`, `Queued`, `ETA` (with `avg Xm Ys / issue` hint).

**B. Execution Plan panel** (left column, top): one row per group. Each row is a 3-column grid:
- Left rail (110px wide, divided from body by a vertical 1px line): `GROUP` label, `G01` cyan display, status row with dot + state name (`active`/`mixed`/`complete`/`queued`/`deploy`).
- Middle: 4-column grid of plan slots. Real issues fill from the left; empty capacity gets dashed placeholder slots. The grid stays 4-wide even when groups have fewer issues — this gives the rows a consistent rhythm and visually communicates max capacity.
- Right rail (140px, divided by a vertical 1px line): `merged/total` tabular display, plus status legend (`● 1 fail · ● 2 ok`).

The final group is the **deploy group** — replaces slots with a single dashed panel reading `▲ DEPLOY · PROMOTE DEV → STAGING → PROD · awaiting groups 1–3`.

**C. Active Agents panel** (left column, below plan): 3-column grid of agent cards for every `in_progress` and `reviewing` issue, plus dimmed "queued" cards for issues waiting on capacity. Each card is the live agent card spec from the component system. Updates every 2s; tool call streams visibly into the mini log.

**D. Side column** (right, 380px wide):
- `ORCHESTRATOR LOG` — system log rows, newest at top, with a pulsing live row at the very top: `<localtime> | live | polling state.json` + cursor blink.
- `STAGES` — current sub-activity per active agent (e.g. `tests · running · ABS-95 · pnpm test widgets`).
- `SIGNALS` — health rail: Linear API, Git worktree pool, Anthropic API, Port pool, Disk, Deploy gate. Each is `<dot> LABEL ........... VALUE` with the dot tone matching health (ok/warn/queued).

### 4. Issue Detail (`02`)

Drill-down for a single issue.

**Top header row** (one bordered panel spanning full width, three columns separated by 1px lines):
- Left (160px): `ISSUE` label · pulse dot + cyan issue id at 22px · `<StatusPill>`.
- Middle: `TITLE` label · 16px white title · two metadata lines underneath (`branch ›` and `worktree ›`).
- Right: action buttons. `← Back` (ghost), then status-dependent actions:
  - Live (`in_progress`/`reviewing`): `▶ RETRY`, `↷ SKIP`, `■ KILL` (danger).
  - Failed: `▶ RETRY` (primary, hard glow).
  - Merged: `View Diff`.

**Main column (left):**
- `ERROR` panel — only renders when `issue.error` is set. Red-bordered, 6%-tinted red background, `!` icon in a square box, uppercase error class header (e.g. `E2E TEST FAILURE`) above the error string. Footer: `attempts N / 2 · review attempts R`.
- `AGENT STREAM` panel — full live log. Each event is one row:
  - Time column (72px, `+HH:MM:SS`, muted).
  - Body. Three kinds:
    - **Tool:** `<tool-name in info-cyan, bold>` followed by `<args in --text-dim>`.
    - **Assistant:** italic body text prefixed by a cyan `▌` block.
    - **Review:** orange `REVIEW · ` prefix followed by the message.
  - When the issue is live, the last row is a synthetic `live` row with a blinking cursor.
- `WORKING DIFF` panel — synthetic unified-diff preview. File path lines are cyan and bold (`›`); hunk headers are info-cyan (`@`); add lines are green with subtle green-tinted background and `+`; remove lines are red with red-tinted background and `−`. Monospace 12px, scroll-clipped to 280px.

**Side column (right, 360px):**
- `METADATA` — kv-grid with two columns: 100px uppercase muted label and a primary value column. Fields: Identifier, Status (pill), Attempts, Reviews, Branch (truncate), Worktree (truncate, 11px), PG Port, API Port, Web Port, PID, Elapsed/Duration.
- `TIMELINE` — vertical timeline as specified in the component system. Stages: Issue planned · Agent spawned · First diff written · Coding (in progress/complete) · Review pass N · Merged to dev / Marked failed. Each row's tone matches the lifecycle phase.
- `ACTIONS` — vertical stack of full-width buttons: `▶ Re-spawn agent`, `↷ Mark as blocked`, `▶ Open worktree in editor`, `▶ Tail JSONL in terminal`, `■ SIGTERM agent process` (danger).

### 5. Launch (`03`)

Form to start a new run. Two columns.

**Left column:**
- `LAUNCH CONFIGURATION` panel — series of `<Field>` rows. Each row has a fixed 200px label column with an uppercase 11px primary label and a small `--text-mute` hint underneath, then the input.
  - **Max parallel agents:** numeric input (80px) + an LED-strip visualizer (8 squares, lit ones filled cyan with soft glow) + `≈ N groups` derived count.
  - **Label filter:** wide text input, max-width 280px. Default `Triaged`.
  - **Model:** segmented control with `OPUS / SONNET / HAIKU`. Default `OPUS`.
  - **Deploy after merge:** toggle. Default ON. Label changes `ENABLED`/`DISABLED`.
  - **Single-issue override:** text input, placeholder `e.g. ABS-101`. Auto-uppercases on input.
  - **Dry run:** toggle. Default OFF.
- `EFFECTIVE COMMAND` panel — live-rendered shell command reflecting current settings:
  `$ ./scripts/start-night-manager.sh --max-agents 3 --label "Triaged" --model opus --deploy`.

**Right column:**
- `LINEAR QUEUE PREVIEW` panel — sticky-header table of matching issues. Columns: `ID` (cyan, 90px), `Title` (truncate), `Est` (right-aligned 60px). Header: `<dot ok> N matching · label "Triaged"`. Scrolls inside max-height 380px.
- `PLANNED EXECUTION` panel — a series of group cards mirroring the eventual plan layout: each card has a `G01` cyan id and a row of pill-bordered issue id chips. A dashed footer card if Deploy is on: `▲ then DEPLOY · dev → staging → prod`.
- `INITIATE RUN` panel — a footer-style strip with a summary on the left (`{N} issues · {G} groups · model · deploy`) and `CANCEL` (ghost) + `▶ INITIATE RUN` (primary, hard glow) on the right.

### 6. Reports (`04`)

Two columns.

**Left (260px) — `REPORT ARCHIVE`:** vertical list of past runs. Each item: bold date line, then a row of compact `N merged · N failed · N blocked · duration` stats (green/red/amber/mute). Active item: 2px cyan left border, slightly raised background. The first item is the in-progress current run (status: "ongoing", duration: "ongoing").

**Right — `REPORT · <id>`:** rendered markdown viewer.
- `H1`: 22px cyan with soft glow, the run name.
- Lede paragraph in `--text-dim`.
- A 4-cell stat strip below the lede (`Merged / Failed / Blocked / Duration`) with 24px tabular-nums values.
- `H2`: 12px 0.16em tracked uppercase, bottom border, `--text-dim`.
- Section lists: each line is `<id (cyan, 70px)> <txt --text-dim> <meta --text-mute>`, with dotted bottom borders.
- Operator notes section: regular paragraph, line-height 1.7, `--text-dim`.

### 7. Footer strip (global)

Hairline strip at the very bottom, 26px tall, `--bg-1`, top-bordered, 10px uppercase 0.12em tracked. Divided cells with 1px vertical lines:
- `<dot ok> STATE.JSON · OK`
- `<dot ok> LINEAR · 18ms`
- `<dot ok> CLAUDE API · 240ms`
- `<dot warn> DISK · 62%`
- spacer
- `BUILD · 2026.05.22`
- `SYNCED HH:MM:SS` + cursor blink (updates every second)

---

## Interactions & Behavior

### Polling

- The UI must poll `.night-manager/state.json` every 2–5 seconds (start at 2s; back off to 5s if no change). Use SWR or React Query in the Next.js implementation.
- File is small (<50KB) — full read each poll is fine.
- Show the poll cadence in the active agents panel header: `<pulse-dot> LIVE · POLL 2s`.

### Live log streaming

- Each active agent has `.night-manager/logs/{identifier}.jsonl`. New lines append as the agent works; bursts of activity interleave with quiet "thinking" periods.
- The Issue Detail screen tails this file. Use a file-watcher (chokidar) on the server side and stream new events over WebSocket or Server-Sent Events to the client.
- Each event is a Claude Code stream-json entry. Render three kinds:
  - **Tool call:** name + arg summary. Use the tool name as the visible label; truncate args to a one-line summary. Optionally collapsible to show full args.
  - **Assistant text:** italic, cyan `▌` prefix.
  - **Reviewer event:** orange `REVIEW · ` prefix.

### Live elapsed timers

Every `--primary`-colored elapsed/clock readout ticks every 1 second. Use `font-variant-numeric: tabular-nums` so digits don't jitter.

### Status transitions

When an issue changes status (queued → in_progress → reviewing → merged/failed), the plan slot's border color animates smoothly (`transition: border-color 200ms ease`). When an agent card flips from `in_progress` to `reviewing`, the pill changes; do not unmount/remount the card.

### Navigation

- Top nav switches screens.
- Clicking a plan slot or an agent card opens the Issue Detail for that issue.
- Clicking the `←` back button on Issue Detail returns to Dashboard.
- The "JUMP TO" tweaks panel in the prototype is for design-review only — do not port it.

### Actions

The UI does not mutate `state.json` directly. Each action shells out:

- `▶ INITIATE RUN` → `POST /api/run/start` → spawns `./scripts/start-night-manager.sh` with the configured flags.
- `■ KILL` (run-level) → `POST /api/run/kill` → `tmux kill-session -t night-manager`.
- `▶ RETRY` / `↷ SKIP` (issue-level) — **not yet supported**. The brief says future support will write a retry/skip marker into `state.json` that the NM watches for. Stub these buttons today: render them in the UI, attach a click handler that POSTs to a placeholder endpoint, show a toast `Action queued — NM will pick this up on next tick` or `Not yet implemented` until the backend lands.
- `■ SIGTERM agent process` → `POST /api/agent/{id}/kill` → `kill -TERM <pid>` server-side. Pid lives in `state.json:issues[id].pid`.
- `▶ Open worktree in editor` → `open -a "Cursor" <worktree>` (or `code <worktree>`) — local-only.
- `▶ Tail JSONL in terminal` → `open -a Terminal` with a script that tails the file.

### Degraded states

- **No run active:** Dashboard shows the most recent report inline (no plan, no agents), and a prominent `▶ NEW RUN` CTA that opens the Launch screen.
- **state.json unreadable / NM not running:** Footer's `STATE.JSON · OK` chip flips to `STATE.JSON · STALE` in `--warn`. The agent panel renders a single message: `no active run — last seen <ts>`. KPI strip empties to em-dashes.

### Theme switching

The Display Mode switch in the top bar lets the operator flip between Apollo / Vanguard / Red October without reload. Implement as a 3-way segmented control:

- The active option fills with its theme's primary color and renders its label in `--bg` (dark) — the swatch becomes a negative-space cutout.
- Inactive options render in `--text-mute` with a tiny color swatch above each label.
- Persist the selection in `localStorage` keyed by user.
- On change, swap `body[data-theme]` and force a reflow as in the WebKit caveat above.

---

## State Management

### Data model (mirrors `state.json`)

```ts
type Status = 'queued' | 'in_progress' | 'reviewing' | 'merged' | 'failed' | 'blocked';

type Issue = {
  identifier: string;       // "ABS-90"
  title: string;
  status: Status;
  branch: string | null;
  worktree: string | null;
  ports: { pg: number; api: number; web: number } | null;
  session_id: string | null;
  pid: number | null;
  log_file: string | null;
  attempts: number;
  review_attempts: number;
  started_at: string | null;
  completed_at: string | null;
  merged_at: string | null;
  error: string | null;
};

type Group = {
  group: number;
  parallel: string[];   // identifiers, can be empty
  deploy: boolean;
};

type RunState = {
  run_id: string;
  started_at: string;
  config: { max_agents: number; label: string; model: string; deploy: boolean };
  plan: Group[];
  issues: Record<string, Issue>;
};
```

`data.jsx` in this bundle has a fully populated mid-run example you can use as a fixture for component tests.

### Derived state

Compute these client-side from `RunState`; do not store them server-side:

```ts
const counts = groupBy(issues, 'status');
const elapsed = now - started_at;
const processed = counts.merged + counts.failed + counts.blocked;
const avgPerIssue = processed > 0 ? elapsed / processed : 0;
const eta = avgPerIssue * (total - processed);
const activeAgents = counts.in_progress + counts.reviewing;
const groupState = (group) =>
  group.deploy ? 'deploy' :
  group.parallel.every(id => issues[id].status === 'merged') ? 'complete' :
  group.parallel.some(id => ['in_progress', 'reviewing'].includes(issues[id].status)) ? 'active' :
  group.parallel.every(id => issues[id].status === 'queued') ? 'queued' :
  'mixed';
```

### Suggested stack on top of Next.js + Tailwind + shadcn

- **Polling:** `useSWR('/api/state', fetcher, { refreshInterval: 2000 })`.
- **Log tail:** Next.js API route that streams via `text/event-stream`; client uses `EventSource`.
- **Routing:** App Router. Routes: `/`, `/issues/[id]`, `/launch`, `/reports`, `/reports/[id]`.
- **Components:** wrap shadcn's `<Card>` with the corner-tick CSS treatment to build `<Panel>`. Wrap `<Badge>` for `<StatusPill>`. Keep status→tone mapping in a single `lib/status.ts`.
- **Theme:** put all tokens in `globals.css` as CSS custom properties under `:root` (Vanguard). Do not use Tailwind's default palette for these — use `bg-[var(--bg-1)]` etc. or extend the Tailwind config with semantic tokens (`brand`, `surface`, `surface-2`, `line`, `line-2`, etc.).

---

## Assets

The design uses **no raster assets**.

- The brand mark is the inline SVG reticle in `common.jsx` (`<NightManagerMark>`). Reproduce as a React component or as an inline SVG in the layout.
- All other "iconography" is monospace characters (`▶ ■ ↷ ← → ▲ ▌ █ ›`) intentionally — do not substitute icon-font glyphs or lucide icons. The terminal-glyph aesthetic is part of the system.

Google Font import (place in `app/layout.tsx` head):
```
https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;500;600;700&display=swap
```

---

## Acceptance Criteria

Implementation is complete when:

- [ ] All four screens (Dashboard, Issue Detail, Launch, Reports) match the reference at parity for layout, type, color, and spacing.
- [ ] State is read from a live `state.json` polled every 2s; the UI never blocks waiting for it.
- [ ] Live elapsed timers tick every second across the whole UI without layout shift.
- [ ] Issue Detail's agent stream tails the per-issue JSONL via SSE and appends new events in real time.
- [ ] Status colors and the status→tone mapping are consistent everywhere (plan slots, agent cards, pills, timeline, side-bar log).
- [ ] The page horizontal-scrolls below 1280px width and never collapses into a mobile layout — this is a desktop console.
- [ ] No border-radius anywhere except where explicitly specified (none in this design — every corner is square).
- [ ] The footer strip and top bar persist across all screens.
- [ ] Actions invoke real shell commands where supported; stub the "future" issue-level retry/skip with a placeholder endpoint and a toast.

---

## Files in this bundle

| File | What it is |
|---|---|
| `Night Manager.html` | Root prototype — load this in a browser to view the design |
| `app.jsx` | App shell, nav, top bar, footer, screen routing, theme switcher |
| `common.jsx` | Shared primitives: `<Panel>`, `<KPI>`, `<StatusPill>`, `<Dot>`, hooks (`useFakeNow`, `useStreamingLog`), formatters (`fmtElapsed`, `fmtClock`) |
| `dashboard.jsx` | Dashboard screen: KPI strip, plan timeline, agent board, side log/signals |
| `issue-detail.jsx` | Issue detail screen: header, error panel, live log, diff preview, sidebar |
| `launch.jsx` | Launch/configure form with effective-command preview and Linear queue preview |
| `report.jsx` | Report archive + rendered markdown viewer |
| `data.jsx` | Hardcoded mid-run fixture data (state, logs, system log, past reports, Linear queue) |
| `tweaks-panel.jsx` | Floating theme/screen switcher used only during design review — **do not port** |

To browse the prototype: open `Night Manager.html` in any modern browser. To switch between Apollo/Vanguard/Phosphor themes interactively, open the floating tweaks panel in the bottom-right.
