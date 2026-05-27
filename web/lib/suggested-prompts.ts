// Zone-aware suggestion chips shown above the chat composer.
// Classify the zone code prefix to pick an appropriate set of prompts;
// fall back to generic prompts when no parcel is anchored yet.

import type { ParcelContext } from "@/lib/parcel";

type ZoneFamily =
  | "centre"
  | "established-residential"
  | "residential-townhouse"
  | "high-rise"
  | "residential"
  | "commercial"
  | "industrial"
  | "unknown";

function classifyZone(code: string): ZoneFamily {
  const c = code.toUpperCase();
  if (c.startsWith("CEN")) return "centre";
  if (c.startsWith("ER")) return "established-residential";
  if (c.startsWith("RT")) return "residential-townhouse";
  if (c.startsWith("HR")) return "high-rise";
  if (/^R-?\d/.test(c)) return "residential";
  if (/^C-?\d/.test(c) || c.startsWith("C1") || c.startsWith("C2") || c.startsWith("C3")) return "commercial";
  if (c.startsWith("I-") || c.startsWith("I1") || c.startsWith("I2")) return "industrial";
  return "unknown";
}

// Adjacent zone for "Compare to X limits" chip — step up one numeric suffix.
function adjacentZone(code: string, prefix: string): string | null {
  const match = code.match(/(\d+)$/);
  if (!match) return null;
  const n = parseInt(match[1], 10);
  return `${prefix}-${n + 1}`;
}

export function getSuggestedPrompts(parcel: ParcelContext | null): string[] {
  if (!parcel?.zone) {
    return [
      "What does the yard look like?",
      "Generate a massing study",
      "What permits will I need?",
      "What are the permitted uses here?",
    ];
  }

  const { code } = parcel.zone;
  const family = classifyZone(code);

  switch (family) {
    case "centre": {
      const adj = adjacentZone(code, "CEN");
      return [
        "What are the built-form standards?",
        "How does bonus zoning work here?",
        adj ? `Compare to ${adj} limits` : "What is the maximum density?",
        "What permits will I need?",
      ];
    }

    case "established-residential":
      return [
        "What does the yard look like?",
        "Compare to RT-2 limits",
        "What permits will I need?",
        "Generate a massing study",
      ];

    case "residential-townhouse": {
      const adj = adjacentZone(code, "ER");
      return [
        "What are the townhouse built-form standards?",
        "What does the yard look like?",
        adj ? `Compare to ${adj} limits` : "What is the maximum lot coverage?",
        "What permits will I need?",
      ];
    }

    case "high-rise":
      return [
        "What is the maximum height here?",
        "How does bonus zoning work here?",
        "What are the shadow impact rules?",
        "What permits will I need?",
      ];

    case "residential":
      return [
        "What does the yard look like?",
        "Generate a massing study",
        "What is the maximum lot coverage?",
        "What permits will I need?",
      ];

    case "commercial":
      return [
        "What are the permitted uses here?",
        "What are the built-form standards?",
        "What is the maximum FAR?",
        "What permits will I need?",
      ];

    case "industrial":
      return [
        "What are the permitted industrial uses?",
        "What are the setback requirements?",
        "What is the maximum lot coverage?",
        "What permits will I need?",
      ];

    default:
      return [
        "What does the yard look like?",
        "Generate a massing study",
        `Compare to nearby ${code} limits`,
        "What permits will I need?",
      ];
  }
}
