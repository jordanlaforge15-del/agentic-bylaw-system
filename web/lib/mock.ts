// Home page sample Q&A fixtures. Each entry is a verified reading from
// the HRM Regional Centre Land Use By-law; answers and section numbers
// have been checked against the HRM RCLUB eval suite.

export type SampleReading = {
  addr: string;
  zone: string;
  q: string;
  verdict: string;
  cite: string;
};

export const SAMPLE_READINGS: SampleReading[] = [
  {
    addr: "5184 Morris St",
    zone: "ER-1",
    q: "What's the maximum lot coverage on this parcel?",
    verdict: "35% — just over 420 m² on a standard lot.",
    cite: "§ 7",
  },
  {
    addr: "2310 Gottingen St",
    zone: "CEN-1",
    q: "Is off-street parking required for a new development?",
    verdict: "No — none required in this zone.",
    cite: "§ 120(b)",
  },
  {
    addr: "1208 Robie St",
    zone: "HR-1",
    q: "What's the maximum building height?",
    verdict: "20.0 m by-right.",
    cite: "§ 7",
  },
];

export type ProofItem = {
  addr: string;
  q: string;
  a: string;
  cite: string;
  accent?: boolean;
};

export const PROOF: ProofItem[] = [
  {
    addr: "5184 Morris St · ER-1",
    q: "Max lot coverage?",
    a: "35%.",
    cite: "HRM LUB § 7",
    accent: true,
  },
  {
    addr: "1208 Robie St · HR-1",
    q: "Max height?",
    a: "20.0 m by-right.",
    cite: "HRM LUB § 7",
  },
  {
    addr: "2310 Gottingen St · CEN-1",
    q: "Parking required?",
    a: "None required.",
    cite: "HRM LUB § 120(b)",
  },
  {
    addr: "17 Edward St · ER-2",
    q: "Home occupation + secondary suite?",
    a: "Both permitted concurrently.",
    cite: "HRM LUB § 49",
  },
  {
    addr: "46 Crichton Ave · ER-1",
    q: "Single-unit dwelling?",
    a: "Permitted as-of-right.",
    cite: "HRM LUB § 5",
  },
  {
    addr: "101 Quinpool Rd · CEN-1",
    q: "Multi-unit dwelling?",
    a: "Permitted.",
    cite: "HRM LUB § 5",
  },
];

export type Thread = {
  id: string;
  addr: string;
  zone: string;
  title: string;
  preview: string;
  updated: string;
  active?: boolean;
  unread?: boolean;
};

export const SAMPLE_THREADS: Thread[] = [
  {
    id: "t1",
    addr: "5184 Morris St",
    zone: "ER-1",
    title: "Maximum lot coverage",
    preview: "35% — just over 420 m².",
    updated: "2m ago",
    active: true,
  },
  {
    id: "t2",
    addr: "1208 Robie St",
    zone: "HR-1",
    title: "Maximum building height",
    preview: "20.0 m by-right.",
    updated: "1h ago",
  },
  {
    id: "t3",
    addr: "17 Edward St",
    zone: "ER-2",
    title: "Home occupation + secondary suite",
    preview: "Both permitted concurrently.",
    updated: "Yesterday",
    unread: true,
  },
  {
    id: "t4",
    addr: "2310 Gottingen St",
    zone: "CEN-1",
    title: "Off-street parking",
    preview: "None required — § 120(b).",
    updated: "2d ago",
  },
  {
    id: "t5",
    addr: "46 Crichton Ave",
    zone: "ER-1",
    title: "Single-unit dwelling",
    preview: "Permitted as-of-right.",
    updated: "3d ago",
  },
  {
    id: "t6",
    addr: "101 Quinpool Rd",
    zone: "CEN-1",
    title: "Multi-unit dwelling",
    preview: "Permitted.",
    updated: "5d ago",
  },
];

export type AgentSource = {
  title: string;
  section: string;
  date: string;
};

export type AgentReasoningStep = {
  n: string;
  cite: string;
  body: string;
};

export type AgentMessage = {
  kind: "agent";
  answer: string;
  body: string;
  reasoning: AgentReasoningStep[];
  confidence: number;
  sources: AgentSource[];
  /** DB primary key of the ChatMessage row, if known. Used by feedback UI. */
  messageDbId?: number;
};

export type SystemMessage = { kind: "system"; body: string };
export type UserMessage = { kind: "user"; body: string };
export type Message = SystemMessage | UserMessage | AgentMessage;

export const SAMPLE_MESSAGES: Message[] = [
  {
    kind: "system",
    body: "Reading 5184 Morris St · ER-1 (Established Residential, Type 1).",
  },
  {
    kind: "user",
    body: "What's the maximum lot coverage on this parcel? We're planning a rear addition.",
  },
  {
    kind: "agent",
    answer: "35% — just over 420 m² on a standard lot.",
    body: "Maximum lot coverage in ER-1 is 35% of the lot area. On a typical 1,200 m² lot that's 420 m² of combined building footprint. Measure the existing footprint first — additions count toward the cap.",
    reasoning: [
      {
        n: "01",
        cite: "§ 7",
        body: "Maximum lot coverage in ER-1 is 35% of the lot area.",
      },
      {
        n: "02",
        cite: "§ 7",
        body: "Lot coverage includes all buildings on the lot — principal dwelling, accessory structures, and any proposed addition.",
      },
      {
        n: "03",
        cite: "§ 3",
        body: "Lot coverage is measured from exterior wall faces, not including eaves or roof overhangs.",
      },
    ],
    confidence: 0.96,
    sources: [
      {
        title: "HRM Regional Centre Land Use By-law",
        section: "§ 7 — Dimensional Standards",
        date: "2025-11-04",
      },
    ],
  },
  { kind: "user", body: "And if we wanted to add a secondary suite too?" },
  {
    kind: "agent",
    answer: "Permitted — counts toward lot coverage.",
    body: "A secondary suite is permitted in ER-1 as an accessory use. Its footprint (if it's a detached structure) counts toward the 35% lot coverage cap, so you'd need to ensure the combined footprint of the addition and any suite structure stays within that limit.",
    reasoning: [
      {
        n: "01",
        cite: "§ 49",
        body: "A secondary suite may be combined with a single-unit dwelling in ER-1.",
      },
      {
        n: "02",
        cite: "§ 7",
        body: "All buildings on the lot — including accessory structures — count toward the 35% lot coverage maximum.",
      },
    ],
    confidence: 0.94,
    sources: [
      {
        title: "HRM Regional Centre Land Use By-law",
        section: "§ 49 — Combination of Uses",
        date: "2025-11-04",
      },
      {
        title: "HRM Regional Centre Land Use By-law",
        section: "§ 7 — Dimensional Standards",
        date: "2025-11-04",
      },
    ],
  },
];

export const SUGGESTED_PROMPTS: string[] = [
  "What does the yard look like?",
  "Generate a massing study",
  "What permits will I need?",
  "Compare to RT-2 limits",
];
