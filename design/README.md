# Design handoffs

Design reference packages exported from the design tool. Each subfolder is a
**self-contained, point-in-time handoff** for one scope — a `README.md`
(spec), `design_files/` (HTML/JSX browser prototypes), and usually
`screenshots/`. These are **reference, not production code**: they document
intent (layout, copy, color, type, interaction) that gets reimplemented in
`web/` against the app's real patterns. The JSX prototypes share a global
lexical scope and cross-reference each other, so each folder is meant to run
standalone — do not merge distinct exports into one tree.

## Convention

- **One folder per scope/export.** Keep folder names short (`authorized-shell`,
  not `design_handoff_authorized_shell`).
- **Commit the whole export** — `README.md`, `design_files/`, and
  `screenshots/` — so the source that drove a change is preserved alongside the
  code. (A drop that isn't committed gets lost; see `authorized-shell` below.)
- **Updating a surface** → replace that folder's contents in place on a branch
  and let the git diff tell the story; don't add `-v2` siblings.
- **Shared design-system core** (tokens, logo, type) is currently duplicated as
  `theme.jsx` across exports so each prototype runs standalone. If that
  duplication becomes a maintenance cost, factor it into a single
  `design/_system/` that handoffs reference.

## Index

| Folder | Scope | Drove |
|--------|-------|-------|
| [`abs-website/`](abs-website/) | Master marketing-site + product-shell handoff; the design-token / logo / type system source of truth the other handoffs build on. | Foundational site + product shell. |
| [`marketing-pages/`](marketing-pages/) | Marketing & legal pages — home, pricing, about, terms, privacy, coverage, changelog, support, citations. | Marketing/legal surface + the question-based pricing model. |
| [`authorized-shell/`](authorized-shell/) | Authorized navigation shell (workspace menu + AuthBar/AuthFooter) and the redesigned **Open a case** surface. | **ABS-334** (authorized nav shell + Open a case redesign). ⚠️ Partially restored — see [`design_files/_PROVENANCE.md`](authorized-shell/design_files/_PROVENANCE.md): the original export's screenshots + several `design_files/` were never committed and need a fresh export. |
| [`night-manager/`](night-manager/) | Night Manager Mission Console — the separate internal ops/orchestration tool (its own repo at runtime). | Night Manager UI. |
