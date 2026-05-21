// /terms — terms of use. Same LegalShell as /privacy.

import { LegalShell, Section } from "@/components/marketing/legal-shell";

const SECTIONS: Section[] = [
  {
    id: "tm-1",
    n: "1.0",
    t: "Acceptance",
    body: [
      {
        k: "p",
        v: "By creating an account or using the ABS application, you agree to these terms and to our Privacy Policy.",
      },
      {
        k: "p",
        v: "If you are accepting on behalf of an organization, you confirm you have authority to bind that organization.",
      },
    ],
  },
  {
    id: "tm-2",
    n: "2.0",
    t: "The service",
    body: [
      {
        k: "p",
        v: "ABS provides an agent that reads consolidated municipal by-laws, applies them to a parcel you supply, and returns a sourced reading.",
      },
      {
        k: "p",
        v: "During private beta the service is limited to the Halifax Regional Municipality. We will add jurisdictions per the published Coverage roadmap.",
      },
    ],
  },
  {
    id: "tm-3",
    n: "3.0",
    t: "Accounts & access",
    body: [
      {
        k: "p",
        v: "You are responsible for activity that occurs under your account. Keep your credentials secret. Notify us promptly of any compromise.",
      },
      {
        k: "ul",
        v: [
          "One person per seat. Sharing a seat is not permitted.",
          "You must be at least 16 years old to hold an account.",
          "We may refuse or close accounts that misuse the service.",
        ],
      },
    ],
  },
  {
    id: "tm-4",
    n: "4.0",
    t: "Not legal advice",
    body: [
      {
        k: "p",
        v: "ABS is research, not legal advice. A reading is a structured retrieval of bylaw text plus the agent’s reasoning. It is not, and should not be relied on as, a development permit, a planning approval, or a legal opinion.",
      },
      {
        k: "note",
        v: "Always verify a reading with HRM Planning before submitting a permit application or making an irreversible decision.",
      },
      {
        k: "p",
        v: "You agree that ABS is not liable for decisions you take in reliance on a reading without independent verification.",
      },
    ],
  },
  {
    id: "tm-5",
    n: "5.0",
    t: "Acceptable use",
    body: [
      { k: "p", v: "You may not:" },
      {
        k: "ul",
        v: [
          "Use ABS to scrape, mirror, or rebuild a competing bylaw retrieval service.",
          "Use ABS for anything illegal, defamatory, or harmful.",
          "Attempt to circumvent rate limits, seats, or security controls.",
        ],
      },
    ],
  },
  {
    id: "tm-6",
    n: "6.0",
    t: "Payment & cancellation",
    body: [
      {
        k: "p",
        v: "Paid plans bill monthly in advance, in CAD. You may cancel at any time from the Billing page; the cancellation takes effect at the end of the current billing period.",
      },
      {
        k: "p",
        v: "Beta pricing is locked for the first 12 months on any plan started before public launch.",
      },
    ],
  },
  {
    id: "tm-7",
    n: "7.0",
    t: "Confidentiality",
    body: [
      {
        k: "p",
        v: "We treat your reading inputs as confidential. We will only disclose them where required by law and, where lawful, will tell you about the request first.",
      },
    ],
  },
  {
    id: "tm-8",
    n: "8.0",
    t: "Liability",
    body: [
      {
        k: "p",
        v: "To the maximum extent permitted by law, ABS’s aggregate liability for any claim arising out of or related to the service is limited to the amount you paid us in the 12 months before the claim arose.",
      },
      {
        k: "p",
        v: "We do not exclude liability for fraud, gross negligence, or anything that cannot be excluded by law.",
      },
    ],
  },
  {
    id: "tm-9",
    n: "9.0",
    t: "Governing law",
    body: [
      {
        k: "p",
        v: "These terms are governed by the laws of the Province of Nova Scotia and the federal laws of Canada applicable therein. The courts of Nova Scotia have exclusive jurisdiction.",
      },
    ],
  },
  {
    id: "tm-10",
    n: "10.0",
    t: "Changes",
    body: [
      {
        k: "p",
        v: "We will email any material change at least 30 days before it takes effect. Continued use of the service after the change takes effect constitutes acceptance.",
      },
    ],
  },
];

export default function TermsPage() {
  return (
    <LegalShell
      kicker="POLICY · TERMS OF USE"
      title="Terms of use."
      sub="The agreement between you and ABS. We have tried to keep it short and to the point. Please read § 4 carefully — it covers the limits of what an ABS reading can be used for."
      plainSummary="ABS produces research, not legal advice. Always verify with the municipality before relying on a reading for a permit decision. We bill monthly, cancel anytime, and won’t sue you for using ABS reasonably."
      consolidatedAt="May 14, 2026"
      version="3.0"
      sections={SECTIONS}
    />
  );
}
