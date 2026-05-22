// /privacy — privacy policy. Content + structure routed through the
// shared LegalShell (sidebar TOC + plain-English summary banner).
//
// Scope of truthfulness: this is a beta-era privacy statement. It
// reflects how the deployment is actually set up today (Clerk auth on
// a Hetzner VPS with Postgres in the same VPS), not an aspirational
// enterprise posture. It is not a substitute for counsel-reviewed
// policy — flagged in the closing note.

import { LegalShell, Section } from "@/components/marketing/legal-shell";

const SECTIONS: Section[] = [
  {
    id: "pv-1",
    n: "1.0",
    t: "Who this is from",
    body: [
      {
        k: "p",
        v: 'This page explains how Agentic Bylaw Systems ("ABS", "we") handles information when you use our website, the ABS application, or contact us by email.',
      },
      {
        k: "p",
        v: "ABS is currently operated as a sole proprietorship out of Halifax, Nova Scotia. The application is in private beta.",
      },
    ],
  },
  {
    id: "pv-2",
    n: "2.0",
    t: "What we collect",
    body: [
      {
        k: "p",
        v: "We collect three categories of information, all of them in service of producing a reading you can trust:",
      },
      {
        k: "ul",
        v: [
          "Account information: name and email address (handled by our auth provider), and your acceptance of the Terms.",
          "Reading inputs: the parcel or address you're researching, the questions you ask the agent, and the agent's response history.",
          "Diagnostic data: timing, errors, and the agent's intermediate retrieval / reasoning steps — used to debug bad readings.",
        ],
      },
    ],
  },
  {
    id: "pv-3",
    n: "3.0",
    t: "How we use it",
    body: [
      {
        k: "p",
        v: "Account information is used to authenticate you and bill you (where billing is enabled on your deployment). Reading inputs and diagnostic data are used to produce readings, to investigate bad readings you report, and to improve the agent.",
      },
      {
        k: "note",
        v: "We do not sell your information. We do not use the contents of your readings or your questions to train third-party foundation models.",
      },
    ],
  },
  {
    id: "pv-4",
    n: "4.0",
    t: "Service providers",
    body: [
      {
        k: "p",
        v: "ABS runs on a small number of third-party services. We will keep this list current as the production stack changes:",
      },
      {
        k: "ul",
        v: [
          "Hosting: Hetzner Cloud (Germany). The application server and Postgres database run on a single virtual server.",
          "Authentication: Clerk. Account creation, email verification, and session JWTs go through Clerk.",
          "LLM inference: Anthropic. Question text and retrieved bylaw fragments are sent to the model to generate the reading.",
          "Payments (when enabled): Stripe. We never see or store your card details.",
        ],
      },
      {
        k: "p",
        v: "Each of these providers has its own privacy terms. If billing is not enabled on your deployment, Stripe does not receive any data about you.",
      },
    ],
  },
  {
    id: "pv-5",
    n: "5.0",
    t: "Where data lives, and for how long",
    body: [
      {
        k: "p",
        v: "Account, reading, and diagnostic data are stored in Postgres on our Hetzner-hosted server (Germany). LLM inference calls are made to Anthropic; the request payload (your question and the retrieved bylaw fragments) leaves our server for that call.",
      },
      {
        k: "p",
        v: "We retain reading history for the life of your account so you can re-open past cases. Deleting a thread from the app removes it from the live database. We do not currently publish a backup-purge SLA — during beta our backups are manual and short-lived.",
      },
    ],
  },
  {
    id: "pv-6",
    n: "6.0",
    t: "Your rights",
    body: [
      {
        k: "p",
        v: "You can request a copy of, or the deletion of, any data we hold about you. Email the request from the address associated with your account.",
      },
      {
        k: "p",
        v: "Beta means turnaround on these requests is hands-on rather than automated; we will respond as quickly as we can.",
      },
    ],
  },
  {
    id: "pv-7",
    n: "7.0",
    t: "Children",
    body: [
      {
        k: "p",
        v: "ABS is not intended for, and we do not knowingly collect information from, anyone under the age of 16.",
      },
    ],
  },
  {
    id: "pv-8",
    n: "8.0",
    t: "Changes",
    body: [
      {
        k: "p",
        v: "We will update the consolidation date at the top of this page when we change this policy. Material changes will trigger a re-acceptance prompt on next login, the same way the Terms do.",
      },
      {
        k: "note",
        v: "This document has not been reviewed by counsel and will be replaced with a counsel-reviewed version before general availability. Questions in the meantime: info@agenticbylawsystems.com.",
      },
    ],
  },
];

export default function PrivacyPage() {
  return (
    <LegalShell
      kicker="POLICY · PRIVACY"
      title="Privacy."
      sub="What we collect, what we don't, and which third-party services see what during private beta."
      plainSummary="We collect what's needed to run readings — your account, the questions you ask, the parcels you research, and diagnostic traces. We don't sell anything. We don't use your readings to train foundation models. This is a beta-era policy and will be replaced by a counsel-reviewed version before GA."
      consolidatedAt="May 21, 2026"
      version="beta-1"
      sections={SECTIONS}
    />
  );
}
