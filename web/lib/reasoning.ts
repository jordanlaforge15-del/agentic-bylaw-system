// Project a tool_use block (Anthropic-shape) into the AgentReasoningStep
// row that the chat pane renders inside the "▸ N reasoning steps"
// dropdown. Used by the page's translateHistory walker to build the
// reasoning list for each agent turn.
//
// One step per tool call. The `cite` column is constrained to a
// fixed 76px lane in the UI, so we use short ALL-CAPS labels there
// (SEARCH / LOOKUP / OUTLINE / LIST) and put the actual call detail
// in the wider `body` column.
//
// Body humanisation, by tool:
//   search_bylaw_evidence  → "<query>" [· @ <civic> <street>] [· doc N] [· path X]
//   lookup_citation        → <citation_path> [· doc N]
//   get_document_outline   → document N
//   list_documents         → (no input)
// Anything unknown falls through to a JSON.stringify of the input,
// truncated to 80 chars — better than dropping the call.

import type { AgentReasoningStep } from "./mock";

type ToolInput = Record<string, unknown> | null | undefined;

export function humanizeToolUse(
  name: string,
  input: ToolInput,
  indexZeroBased: number,
): AgentReasoningStep {
  const n = String(indexZeroBased + 1).padStart(2, "0");
  return {
    n,
    cite: shortToolLabel(name),
    body: humanizeInput(name, input ?? {}),
  };
}

function shortToolLabel(name: string): string {
  switch (name) {
    case "search_bylaw_evidence":
      return "SEARCH";
    case "lookup_citation":
      return "LOOKUP";
    case "get_document_outline":
      return "OUTLINE";
    case "list_documents":
      return "LIST";
    default:
      // Unknown tool: take the first 8 chars uppercased so the cite
      // lane stays a fixed width.
      return name.toUpperCase().slice(0, 8);
  }
}

function humanizeInput(name: string, input: Record<string, unknown>): string {
  switch (name) {
    case "search_bylaw_evidence":
      return humanizeSearch(input);
    case "lookup_citation":
      return humanizeLookup(input);
    case "get_document_outline":
      return `document ${pickStr(input, "document_id") ?? "—"}`;
    case "list_documents":
      return "(no input)";
    default: {
      const raw = JSON.stringify(input);
      return raw.length > 80 ? `${raw.slice(0, 77)}…` : raw;
    }
  }
}

function humanizeSearch(input: Record<string, unknown>): string {
  const query = pickStr(input, "query") ?? "";
  const parts: string[] = [`"${query}"`];
  const location = input.location as
    | { civic_number?: unknown; street?: unknown }
    | null
    | undefined;
  if (
    location &&
    typeof location.civic_number === "string" &&
    typeof location.street === "string"
  ) {
    parts.push(`@ ${location.civic_number} ${location.street}`);
  }
  const docId = pickStr(input, "document_id");
  if (docId) parts.push(`doc ${docId}`);
  const path = pickStr(input, "citation_path_prefix");
  if (path) parts.push(`path "${path}"`);
  return parts.join(" · ");
}

function humanizeLookup(input: Record<string, unknown>): string {
  const path = pickStr(input, "citation_path") ?? "—";
  const docId = pickStr(input, "document_id");
  return docId ? `${path} · doc ${docId}` : path;
}

function pickStr(
  obj: Record<string, unknown>,
  key: string,
): string | null {
  const v = obj[key];
  if (typeof v === "string" && v.length > 0) return v;
  if (typeof v === "number" && Number.isFinite(v)) return String(v);
  return null;
}
