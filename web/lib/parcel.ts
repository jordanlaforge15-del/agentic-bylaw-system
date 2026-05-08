// Project a saved chat session's tool_use / tool_result rounds into
// the structured parcel context the right pane renders.
//
// The advisor's `search_bylaw_evidence` tool, when called with a
// `location` slot, returns a response whose `matches[*].linked_datasets`
// carry the real attribute data from the spatial join. The fields we
// care about live two levels deep:
//
//   matches[*].linked_datasets[*].name
//   matches[*].linked_datasets[*].location_resolver
//   matches[*].linked_datasets[*].location_confidence
//   matches[*].linked_datasets[*].feature_matches[0].canonical_attributes
//
// Different datasets contribute different attribute keys (verified
// against the live data — `SELECT jsonb_object_keys` per dataset):
//   halifax_zoning_boundaries     → zone_code, zone_description
//   halifax_height_precincts      → max_height_m
//   halifax_heritage_districts    → district_name, district_status
//                                   (status codes: PRP=Proposed,
//                                    ACT=Active, REG=Registered)
//   halifax_far_precincts         → max_far
//   halifax_bonus_zoning_districts → district_name, district_code
//   halifax_shadow_impact_areas   → impact_area
//
// We dedupe by dataset name (taking the most recent search response in
// the conversation) and surface only the attributes the UI knows how
// to display. Anything else lives untouched in `extras` for future
// rows.

export type ParcelContext = {
  // Civic address as the LLM populated it in the tool call.
  address: { civic_number: string; street: string };
  // Lat/lng + the raw geocoder confidence, when available.
  geocode: {
    latitude: number;
    longitude: number;
    confidence: number;
    resolver: string;
  } | null;
  zone: { code: string; description: string } | null;
  height: { max_m: number | null } | null;
  heritage: { name: string; status: string } | null;
  far: { max: number | null } | null;
  bonus: { name: string } | null;
  shadow: { area: string } | null;
  // Citations cited in the same conversation. Used by the right
  // pane's "CITED THIS THREAD" block.
  cited: Array<{ citation: string; title: string; date?: string }>;
};

export type BackendBlock =
  | { type: "text"; text: string }
  | {
      type: "tool_use";
      id: string;
      name: string;
      input?: Record<string, unknown> | null;
    }
  | { type: "tool_result"; tool_use_id: string; content: unknown };

export type BackendMessage = {
  role: "user" | "assistant";
  content: string | BackendBlock[];
};

type LinkedDataset = {
  name?: string | null;
  location_resolver?: string | null;
  location_confidence?: number | null;
  feature_matches?: Array<{
    canonical_attributes?: Record<string, unknown> | null;
  }> | null;
};

type ToolResultPayload = {
  matches?: Array<{
    citation_path?: string | null;
    citation_label?: string | null;
    bylaw_name?: string | null;
    fragment_type?: string | null;
    linked_datasets?: LinkedDataset[] | null;
  }> | null;
};

const ZONING = "halifax_zoning_boundaries";
const HEIGHT = "halifax_height_precincts";
const HERITAGE = "halifax_heritage_districts";
const FAR = "halifax_far_precincts";
const BONUS = "halifax_bonus_zoning_districts";
const SHADOW = "halifax_shadow_impact_areas";

export function extractParcelContext(
  messages: BackendMessage[],
): ParcelContext | null {
  // Walk the messages newest-first. A "useful" turn is the most
  // recent search_bylaw_evidence tool_use whose tool_result actually
  // carried linked_datasets — i.e. the LLM gave it a `location` and
  // got a spatial hit back. We project just that turn.
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const m = messages[i];
    if (m.role !== "assistant" || typeof m.content === "string") continue;
    for (const block of m.content) {
      if (block.type !== "tool_use") continue;
      if (block.name !== "search_bylaw_evidence") continue;
      const input = (block.input ?? {}) as Record<string, unknown>;
      const location = input.location as
        | { civic_number?: unknown; street?: unknown }
        | undefined;
      if (
        !location ||
        typeof location.civic_number !== "string" ||
        typeof location.street !== "string"
      ) {
        continue;
      }
      // Find the matching tool_result in the *next* message.
      const result = findToolResult(messages, i, block.id);
      if (!result) continue;
      const ctx = projectToolResult(
        location.civic_number,
        location.street,
        result,
        messages,
      );
      if (ctx) return ctx;
    }
  }
  return null;
}

function findToolResult(
  messages: BackendMessage[],
  fromIdx: number,
  toolUseId: string,
): ToolResultPayload | null {
  for (let j = fromIdx + 1; j < messages.length; j += 1) {
    const m = messages[j];
    if (m.role !== "user" || typeof m.content === "string") continue;
    for (const block of m.content) {
      if (block.type !== "tool_result") continue;
      if (block.tool_use_id !== toolUseId) continue;
      if (typeof block.content !== "string") return null;
      try {
        return JSON.parse(block.content) as ToolResultPayload;
      } catch {
        return null;
      }
    }
  }
  return null;
}

function projectToolResult(
  civic_number: string,
  street: string,
  payload: ToolResultPayload,
  allMessages: BackendMessage[],
): ParcelContext | null {
  const matches = payload.matches ?? [];
  if (matches.length === 0) return null;

  // Index linked_datasets by dataset name. Keep the first feature_match
  // (the search service already orders them by overlap_metric desc,
  // so this is the most-likely-governing polygon).
  const byName = new Map<
    string,
    { ds: LinkedDataset; attrs: Record<string, unknown> }
  >();
  let geocode: ParcelContext["geocode"] = null;
  for (const match of matches) {
    for (const ds of match.linked_datasets ?? []) {
      if (!ds.name) continue;
      const fm = (ds.feature_matches ?? [])[0];
      const attrs = fm?.canonical_attributes ?? {};
      if (!byName.has(ds.name)) {
        byName.set(ds.name, { ds, attrs });
      }
      // First populated location_confidence wins.
      if (geocode === null && typeof ds.location_confidence === "number") {
        geocode = {
          // Coordinates aren't returned in the LLM-facing payload —
          // the geocoder result lives only in the request side. We
          // leave lat/lng null until we plumb it through; confidence
          // and resolver are enough for the demo caption.
          latitude: NaN,
          longitude: NaN,
          confidence: ds.location_confidence,
          resolver: ds.location_resolver ?? "unknown",
        };
      }
    }
  }

  const zoning = byName.get(ZONING);
  const heightDs = byName.get(HEIGHT);
  const heritageDs = byName.get(HERITAGE);
  const farDs = byName.get(FAR);
  const bonusDs = byName.get(BONUS);
  const shadowDs = byName.get(SHADOW);

  // If we got a geocode hit but no zone match (impossible at the
  // moment — every parcel has a zone — but defensible) we still
  // return what we have so the address renders.
  return {
    address: { civic_number, street },
    geocode,
    zone: zoning
      ? {
          code: pickStr(zoning.attrs, "zone_code") || "—",
          description: pickStr(zoning.attrs, "zone_description") || "",
        }
      : null,
    height: heightDs
      ? { max_m: pickNum(heightDs.attrs, "max_height_m") }
      : null,
    heritage: heritageDs
      ? {
          name: pickStr(heritageDs.attrs, "district_name") || "—",
          status: humanizeHeritageStatus(
            pickStr(heritageDs.attrs, "district_status"),
          ),
        }
      : null,
    far: farDs ? { max: pickNum(farDs.attrs, "max_far") } : null,
    bonus: bonusDs
      ? { name: pickStr(bonusDs.attrs, "district_name") || "—" }
      : null,
    shadow: shadowDs
      ? { area: pickStr(shadowDs.attrs, "impact_area") || "—" }
      : null,
    cited: collectCitations(allMessages),
  };
}

function pickStr(
  obj: Record<string, unknown>,
  key: string,
): string | null {
  const v = obj[key];
  return typeof v === "string" && v.length > 0 ? v : null;
}

function humanizeHeritageStatus(code: string | null): string {
  if (!code) return "";
  const map: Record<string, string> = {
    PRP: "Proposed",
    ACT: "Active",
    REG: "Registered",
  };
  return map[code] || code;
}

function pickNum(
  obj: Record<string, unknown>,
  key: string,
): number | null {
  const v = obj[key];
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string" && v.length > 0) {
    const n = Number(v);
    if (Number.isFinite(n)) return n;
  }
  return null;
}

// Dedupe-by-citation walk over every search_bylaw_evidence result
// in the conversation. We keep only the first hit per citation_path
// because the LLM tends to repeat the same fragment across follow-up
// queries.
function collectCitations(
  messages: BackendMessage[],
): ParcelContext["cited"] {
  const seen = new Map<string, ParcelContext["cited"][number]>();
  for (const m of messages) {
    if (m.role !== "user" || typeof m.content === "string") continue;
    for (const block of m.content) {
      if (block.type !== "tool_result") continue;
      if (typeof block.content !== "string") continue;
      let payload: ToolResultPayload;
      try {
        payload = JSON.parse(block.content) as ToolResultPayload;
      } catch {
        continue;
      }
      for (const match of payload.matches ?? []) {
        const cite =
          (match.citation_path && match.citation_path.trim()) ||
          (match.citation_label && match.citation_label.trim()) ||
          "";
        if (!cite || cite === "schedules.zoning") continue;
        if (seen.has(cite)) continue;
        seen.set(cite, {
          citation: cite,
          title:
            match.bylaw_name ||
            match.fragment_type ||
            "HRM Land Use By-law",
        });
      }
    }
  }
  return Array.from(seen.values()).slice(0, 6);
}
