// Sample fixtures lifted from design_files/home.jsx and app-screen.jsx.
// Real HRM streets with fabricated readings — they read better than lorem
// during development. Section numbers approximate the real HRM Land Use
// By-law; a planner needs to verify each fixture before launch.

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
    q: "Can I add a backyard suite?",
    verdict: "Yes — up to 80 m².",
    cite: "§ 9.4",
  },
  {
    addr: "1208 Robie St",
    zone: "COR",
    q: "How tall can I build?",
    verdict: "20 m by-right. Up to 26 m with a bonus.",
    cite: "§ 6.2.3",
  },
  {
    addr: "17 Edward St",
    zone: "ER-2",
    q: "Can I subdivide the lot?",
    verdict: "No — frontage is 1.4 m short.",
    cite: "§ 4.3",
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
    q: "Backyard suite?",
    a: "Yes — up to 80 m².",
    cite: "HRM LUB § 9.4",
    accent: true,
  },
  {
    addr: "1208 Robie St · COR",
    q: "Max height?",
    a: "20 m by-right.",
    cite: "HRM LUB § 6.2.3",
  },
  {
    addr: "17 Edward St · ER-2",
    q: "Subdivide?",
    a: "No — 1.4 m short.",
    cite: "HRM LUB § 4.3",
  },
  {
    addr: "2310 Gottingen St · DH-1",
    q: "Commercial use?",
    a: "Permitted on ground floor.",
    cite: "HRM LUB § 7.1",
  },
  {
    addr: "46 Crichton Ave · DR",
    q: "Side yard?",
    a: "1.2 m minimum.",
    cite: "HRM LUB § 5.4",
  },
  {
    addr: "101 Quinpool Rd · COR",
    q: "Parking minimum?",
    a: "None — within transit zone.",
    cite: "HRM LUB § 8.2",
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
    title: "Backyard suite feasibility",
    preview: "Yes — up to 80 m².",
    updated: "2m ago",
    active: true,
  },
  {
    id: "t2",
    addr: "1208 Robie St",
    zone: "COR",
    title: "Maximum height + bonusing",
    preview: "20 m by-right. Up to 26 m with…",
    updated: "1h ago",
  },
  {
    id: "t3",
    addr: "17 Edward St",
    zone: "ER-2",
    title: "Subdivision check",
    preview: "No — frontage 1.4 m short.",
    updated: "Yesterday",
    unread: true,
  },
  {
    id: "t4",
    addr: "2310 Gottingen St",
    zone: "DH-1",
    title: "Ground-floor commercial",
    preview: "Permitted as primary use.",
    updated: "2d ago",
  },
  {
    id: "t5",
    addr: "46 Crichton Ave",
    zone: "DR",
    title: "Side yard minimums",
    preview: "1.2 m minimum on both sides.",
    updated: "3d ago",
  },
  {
    id: "t6",
    addr: "101 Quinpool Rd",
    zone: "COR",
    title: "Parking minimums",
    preview: "None — within transit zone.",
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
    body: "Can my client add a backyard suite to this lot? They want a 1-bed unit, around 75 m².",
  },
  {
    kind: "agent",
    answer: "Yes — up to 80 m².",
    body: "A backyard suite is permitted as a secondary use in ER-1. Your client's intended size (75 m²) falls under the 80 m² gross-floor-area cap. Two things to confirm before drawings: rear yard depth and habitable separation from the main dwelling.",
    reasoning: [
      {
        n: "01",
        cite: "§ 9.4.1",
        body: "Backyard suites are permitted in ER-1 zones as secondary uses to a single-unit dwelling.",
      },
      {
        n: "02",
        cite: "§ 9.4.3",
        body: "Maximum gross floor area: 80 m². The 75 m² target clears this with 5 m² of margin.",
      },
      {
        n: "03",
        cite: "§ 9.4.5",
        body: "Minimum 1.5 m separation from the principal dwelling. Your site survey shows 4.2 m available — clears.",
      },
      {
        n: "04",
        cite: "§ 5.4",
        body: "Rear yard minimum 4.5 m. Verify against most recent survey before final massing.",
      },
    ],
    confidence: 0.94,
    sources: [
      {
        title: "HRM Land Use By-law",
        section: "§ 9.4 — Backyard Suites",
        date: "2025-11-04",
      },
      {
        title: "HRM Land Use By-law",
        section: "§ 5.4 — Yard Requirements",
        date: "2025-11-04",
      },
    ],
  },
  { kind: "user", body: "What about height? Two storeys?" },
  {
    kind: "agent",
    answer: "One storey, max 4.5 m.",
    body: "ER-1 backyard suites are limited to a single storey with a maximum height of 4.5 m measured from average grade. A two-storey suite would not be by-right — your client would need to apply for a development variance.",
    reasoning: [
      {
        n: "01",
        cite: "§ 9.4.4",
        body: "Backyard suites in ER-1 are limited to one storey.",
      },
      {
        n: "02",
        cite: "§ 9.4.4",
        body: "Maximum height 4.5 m from average grade.",
      },
      {
        n: "03",
        cite: "§ 2.8",
        body: "Variances follow the standard development variance process — typically 6–10 weeks.",
      },
    ],
    confidence: 0.97,
    sources: [
      {
        title: "HRM Land Use By-law",
        section: "§ 9.4 — Backyard Suites",
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
