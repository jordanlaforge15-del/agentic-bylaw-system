# design_handoff_authorized_shell

Design specification for the ABS° authorized navigation shell and the Open a case surface.

## Components covered

- **AuthBar** — the sticky top bar on all signed-in non-app pages
- **AccountMenu** — the workspace dropdown trigger and panel
- **AuthFooter** — the slim footer below the authorized pages
- **AuthShell** — convenience wrapper composing AuthBar + content + AuthFooter
- **OpenCaseForm** — the /cases/new form (QuestionCard, CaseRadio, checkout summary)

## Spacing tokens

All measurements in px. These values are authoritative; the implementation in
`web/components/product/` must match them.

### AuthBar

| Region | Property | Value |
|--------|----------|-------|
| Header | padding | 11px 32px |
| Left cluster (logo → divider → section label) | gap | 18px |
| Between left cluster and AccountMenu trigger | justifyContent: space-between | — |

### AccountMenu trigger chip

| Element pair | Property | Value |
|---|---|---|
| Monogram avatar → name+role text | gap | **11px** |
| Name+role text → chevron | (same flex gap) | 11px |
| Top / bottom padding | py | 4px (py-1) |
| Left padding | pl | 4px (pl-1) |
| Right padding (non-compact) | pr | 12px (pr-3) |
| Avatar size (non-compact, in AuthBar) | width × height | 26 × 26 |

### AccountMenu dropdown panel

| Element pair | Property | Value |
|---|---|---|
| Identity header avatar → name+email | gap | 11px |
| Header padding | px / py | 14px / 14px |
| Nav section row padding | padding | 9px 14px 9px 13px |
| Nav section row: label → active badge | gridTemplateColumns | 1fr auto |

### QuestionCard (open-case-form)

| Element pair | Property | Value |
|---|---|---|
| CaseRadio (16 × 16) → question title | gap | **14px** |
| Card padding | padding | 20px 22px |
| Description paragraph indent (aligns under title) | paddingLeft | 30px (= 16 + 14) |
| Between top row and description | gap (card flex-col) | 10px (gap-2.5) |

### AuthFooter

| Property | Value |
|----------|-------|
| marginTop | 64px |
| padding | 18px 32px |
