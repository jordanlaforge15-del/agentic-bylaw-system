// /app — three-pane chat product shell. The page is fully client-side
// because it owns chat state (messages, thinking indicator) and composer
// interaction. The marketing layout is bypassed via the (product) route
// group + its dedicated layout.tsx.

"use client";

import { useState } from "react";
import { AppHeader } from "@/components/product/app-header";
import { Sidebar } from "@/components/product/sidebar";
import { ChatThread } from "@/components/product/chat-thread";
import { Composer } from "@/components/product/composer";
import { ParcelPane } from "@/components/product/parcel-pane";
import { SAMPLE_MESSAGES, type Message } from "@/lib/mock";

const STEPS = [
  "Locating parcel in HRM cadastre…",
  "Loading Land Use By-law (rev. 2025-11-04)…",
  "Reading § 9.4 — Backyard Suites…",
  "Cross-checking § 5.4 yard requirements…",
  "Compiling answer…",
];

const READING = { addr: "5184 Morris St", zone: "ER-1" };

export default function ProductAppPage() {
  const [messages, setMessages] = useState<Message[]>(SAMPLE_MESSAGES);
  const [thinking, setThinking] = useState(false);
  const [thinkStep, setThinkStep] = useState(0);

  const send = (text: string) => {
    setMessages((prev) => [...prev, { kind: "user", body: text }]);
    setThinking(true);
    setThinkStep(0);
    let i = 0;
    const tick = () => {
      i += 1;
      if (i >= STEPS.length) {
        setMessages((prev) => [
          ...prev,
          {
            kind: "agent",
            answer: "Likely yes — with conditions.",
            body: "Based on the parcel's frontage and yard depths, your follow-up sits within standard ER-1 thresholds. The key contingency is the principal-dwelling separation — please attach a current site survey before final design.",
            reasoning: [
              {
                n: "01",
                cite: "§ 9.4.5",
                body: "Principal-dwelling separation: 1.5 m minimum. Survey required.",
              },
              {
                n: "02",
                cite: "§ 5.4",
                body: "Yard depths within ER-1 standard ranges based on cadastral data.",
              },
              {
                n: "03",
                cite: "§ 2.8",
                body: "Conditional approvals available via standard variance.",
              },
            ],
            confidence: 0.88,
            sources: [
              {
                title: "HRM Land Use By-law",
                section: "§ 9.4",
                date: "2025-11-04",
              },
            ],
          },
        ]);
        setThinking(false);
      } else {
        setThinkStep(i);
        setTimeout(tick, 520 + Math.random() * 240);
      }
    };
    setTimeout(tick, 380);
  };

  const onNew = () => {
    setMessages([
      {
        kind: "system",
        body: "New reading — paste an HRM address to begin.",
      },
    ]);
    setThinking(false);
    setThinkStep(0);
  };

  return (
    <div className="h-screen flex flex-col bg-surface text-text overflow-hidden">
      <AppHeader reading={READING} />
      <div className="flex-1 flex min-h-0">
        <Sidebar onNew={onNew} />
        <main className="flex-1 flex flex-col min-w-0 bg-surface">
          <ChatThread
            messages={messages}
            thinking={thinking}
            thinkSteps={STEPS}
            thinkStep={thinkStep}
          />
          <Composer onSend={send} disabled={thinking} />
        </main>
        <ParcelPane />
      </div>
    </div>
  );
}
