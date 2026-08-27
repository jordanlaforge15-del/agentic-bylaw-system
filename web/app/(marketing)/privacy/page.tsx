// /privacy — privacy policy. Content + structure routed through the
// shared LegalShell (sidebar TOC + plain-English summary banner).
//
// Scope of truthfulness: this is a beta-era privacy statement. It
// describes data handling honestly (data location, retention, the
// third parties that receive user data) without naming infrastructure
// brands.

import type { Metadata } from "next";
import { pageMetadata } from "@/lib/page-metadata";
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
    t: "Third parties that receive your data",
    body: [
      {
        k: "p",
        v: "Operating ABS involves a small number of third-party service providers. Each receives only the data needed for the function they perform:",
      },
      {
        k: "ul",
        v: [
          "Authentication: an identity provider handles account creation, email verification, and session tokens. They see your email address and authentication metadata.",
          "LLM inference: a third-party large-language-model provider receives the request payload — your question text and the bylaw fragments we retrieve — to generate the reading.",
          "Payments (when billing is enabled): a payment processor handles checkout. We never see or store your card details. If billing is not enabled on your deployment, the payment processor does not receive any data about you.",
        ],
      },
      {
        k: "p",
        v: "Each provider operates under its own privacy terms. We will tell you who they are if you ask.",
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
        v: "Account, reading, and diagnostic data are stored in our application's database on a server located in the European Union. LLM inference calls leave our server to reach the model provider; the request payload (your question and the retrieved bylaw fragments) is included.",
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
        k: "p",
        v: "Questions about this policy: info@agenticbylawsystems.com.",
      },
    ],
  },
];

export const metadata: Metadata = pageMetadata({
  path: "/privacy",
  title: "Privacy Policy — ABS°",
  description:
    "How ABS handles your account details, the parcels and questions you research, and diagnostic data: what we keep, for how long, and who receives it.",
});

export default function PrivacyPage() {
  return (
    <LegalShell
      kicker="POLICY · PRIVACY"
      title="Privacy."
      sub="What we collect, what we don't, and which third-party services see what during private beta."
      plainSummary="We collect what's needed to run readings — your account, the questions you ask, the parcels you research, and diagnostic traces. We don't sell anything. We don't use your readings to train foundation models."
      consolidatedAt="May 21, 2026"
      version="beta-1"
      sections={SECTIONS}
    />
  );
}
