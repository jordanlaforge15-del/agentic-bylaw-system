# Provenance — authorized-shell design_files (partial restore)

The original export folder `design/design_handoff_authorized_shell/` (a full
handoff with the detailed README, `design_files/`, and `screenshots/`) was
never committed to git and was later deleted from the working tree. It is not
recoverable from the repository.

## Recovered here (byte-faithful)

These two **new** components were read verbatim during the ABS-334
implementation and reproduced here exactly — they are the authoritative source
the merged code was built from:

- `auth-nav.jsx` — `AccountMenu`, `AuthBar`, `AuthFooter`, `ThemeSwitch`,
  `AUTH_AREA`, `AUTH_NAV`, `AUTH_LABELS`.
- `open-case.jsx` — `OpenCasePage`, `QuestionCard`, `CaseRadio`,
  `ANCHOR_TYPES`, `CASE_QUESTIONS`.

The folder's `README.md` (one level up) is the summary spec and is already
tracked.

## Still missing — needs a fresh export from the design tool

The following were part of the original export and could **not** be restored
(binary screenshots can't be reconstructed; the changed JSX was not read in
full during implementation):

- `screenshots/` — `01-open-a-case-top.png`, `02-open-a-case-checkout.png`,
  `03-billing-authorized-shell.png`, `04-workspace-menu-open.png`,
  `05-open-a-case-setback-dark.png`.
- `design_files/` supporting + changed files: `app.jsx` (changed),
  `app-screen.jsx` (changed), `theme.jsx`, `chrome.jsx`, `pages.jsx`,
  `home.jsx`, `changelog-coverage-support.jsx`, `about-legal.jsx`,
  `citations.jsx`, `tweaks-panel.jsx`.

To complete the restore, re-export `design_handoff_authorized_shell` from the
design tool and drop its `screenshots/` and remaining `design_files/` into this
folder.
