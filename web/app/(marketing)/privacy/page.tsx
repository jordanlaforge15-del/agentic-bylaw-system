// /privacy — privacy policy. Content + structure routed through the
// shared LegalShell (sidebar TOC + plain-English summary banner).

import { LegalShell, Section } from "@/components/marketing/legal-shell";

const SECTIONS: Section[] = [
  {
    id: "pv-1",
    n: "1.0",
    t: "Scope",
    body: [
      {
        k: "p",
        v: 'This policy explains how ABS Reading Inc. ("ABS", "we") handles information when you use our website, the ABS application, or talk to us by email.',
      },
      {
        k: "p",
        v: "It applies to everyone — drafter, practice, and developer tier customers — and to visitors of our public site.",
      },
    ],
  },
  {
    id: "pv-2",
    n: "2.0",
    t: "Information we collect",
    body: [
      {
        k: "p",
        v: "We collect three categories of information, all of them in service of producing a reading you can trust:",
      },
      {
        k: "ul",
        v: [
          "Account information: name, email, organization, billing details.",
          "Reading inputs: addresses, parcel identifiers, and the questions you type into the agent.",
          "Diagnostic data: timing, errors, and the agent’s intermediate steps, so we can debug bad readings.",
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
        v: "Information you give us is used to run the agent, deliver readings back to you, bill you, and maintain the service.",
      },
      {
        k: "p",
        v: "We may use anonymized, aggregated metrics about which sections of a bylaw are most queried — never with your account attached — to prioritize coverage improvements.",
      },
      {
        k: "note",
        v: "We do not use your readings, your questions, or your address inputs to train foundation models. Period.",
      },
    ],
  },
  {
    id: "pv-4",
    n: "4.0",
    t: "Subprocessors",
    body: [
      {
        k: "p",
        v: "We use a small number of subprocessors to operate the service. Each is contractually bound to the same standards we hold ourselves to.",
      },
      {
        k: "ul",
        v: [
          "Cloud hosting: Amazon Web Services (ca-central-1, Montréal).",
          "Payment processing: Stripe.",
          "Email delivery: Postmark.",
          "Geocoding (HRM-only): Open Address Search + HRM Open Data.",
        ],
      },
    ],
  },
  {
    id: "pv-5",
    n: "5.0",
    t: "Data location & retention",
    body: [
      {
        k: "p",
        v: "Customer data is stored in Canada (ca-central-1) and never leaves Canadian jurisdiction during regular operation. Inference may transit other regions; payloads are encrypted in transit.",
      },
      {
        k: "p",
        v: "We retain reading history for the life of your account. You can delete a thread at any time. Deleted data is purged from backups within 35 days.",
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
        v: "You can request a copy of, or the deletion of, any data we hold about you. We will respond within 14 days.",
      },
      {
        k: "p",
        v: "Send the request to privacy@abs.app from the email address associated with the account.",
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
    t: "Changes to this policy",
    body: [
      {
        k: "p",
        v: "We will email any material change at least 30 days before it takes effect. The consolidation date at the top of this page reflects the most recent change.",
      },
    ],
  },
];

export default function PrivacyPage() {
  return (
    <LegalShell
      kicker="POLICY · PRIVACY"
      title="Privacy."
      sub="What we collect, what we don’t, and the small set of cases where information leaves your workspace."
      plainSummary="We collect what’s needed to run readings — your address inputs, the bylaw fragments we retrieve, and your account info. We never sell anything. We never use your reading history to train models."
      consolidatedAt="May 14, 2026"
      version="2.1"
      sections={SECTIONS}
    />
  );
}
