// ABS-451: resolve inline citation references ("(Section 442)",
// "(Schedule 15)", "§ 4.2.1") that appear inside agent prose and table
// cells back to the citation paths the agent actually retrieved this
// thread — the same set the right rail's "CITED THIS THREAD" cards are
// built from.
//
// Why an index rather than a live lookup: the clause-detail endpoint
// takes a *citation path* ("SCHEDULES.SCHEDULE_15", "4.2.1"), not the
// prose label the model writes ("Schedule 15"). Rather than guess at
// path spellings — and rather than make a network call per rendered
// span — we index the thread's known citations under every label form
// they could plausibly be written as, and linkify only the inline
// references that hit that index. Anything unrecognised stays plain
// text: a citation that looks clickable but 404s is worse than one
// that is honestly inert.

export type CitationRef = {
  citation: string;
  title: string;
  date?: string;
};

export type CitationIndex = {
  /** label key → the citation it unambiguously refers to. */
  byKey: Map<string, CitationRef>;
  /** Keys that two or more distinct citations claim — never linkified. */
  ambiguous: Set<string>;
  /** Number of distinct citations indexed. Zero ⇒ nothing to linkify. */
  count: number;
};

/** Structural words that can precede a clause number, mapped to a
 *  canonical singular form. Both the prose side ("Sections 4.2") and
 *  the path side ("SCHEDULES.SCHEDULE_15") run through this. */
const KIND_ALIASES: Record<string, string> = {
  section: "section",
  sections: "section",
  sec: "section",
  s: "section",
  schedule: "schedule",
  schedules: "schedule",
  sched: "schedule",
  part: "part",
  parts: "part",
  clause: "clause",
  clauses: "clause",
  subsection: "subsection",
  subsections: "subsection",
  policy: "policy",
  policies: "policy",
  appendix: "appendix",
  appendices: "appendix",
  article: "article",
  articles: "article",
  division: "division",
  divisions: "division",
  table: "table",
  tables: "table",
};

const KIND_PATTERN =
  "sections?|secs?\\.?|schedules?|parts?|clauses?|sub-?sections?|" +
  "policy|policies|appendix|appendices|articles?|divisions?|tables?";

// A clause number: "442", "4.2.1", "15A", "17(a)", or a roman-numeral
// part ("III"). Trailing sentence punctuation the pattern swallows is
// trimmed off the span afterwards.
const NUM_PATTERN =
  "(?:[0-9][A-Za-z0-9.\\-]*|[IVXLCivxlc]{1,7})(?:\\([A-Za-z0-9]+\\))?";

// Three ways a citation shows up inline:
//   1. kind + number   — "Section 442", "Schedule 15", "Part III"
//   2. § + number      — "§ 4.2.1"
//   3. a bare dotted number — "(4.2.1)"
// Case-insensitive; every hit still has to resolve against the index
// before it becomes a link.
const CITATION_RE = new RegExp(
  `\\b(${KIND_PATTERN})\\s+(${NUM_PATTERN})` +
    `|§\\s*(${NUM_PATTERN})` +
    `|\\b(\\d+(?:\\.\\d+)+(?:\\([A-Za-z0-9]+\\))?)`,
  "gi",
);

/** Split a citation path or prose reference into lowercase tokens.
 *  Hierarchy separators (``.`` ``_`` ``>`` ``|``) become spaces — except
 *  a dot between two digits, which is part of a clause number and must
 *  survive ("schedules.schedule_15" → schedule/15; "4.2.1" → 4.2.1). */
function tokenize(raw: string): string[] {
  const spaced = raw
    .toLowerCase()
    .replace(/\./g, (_match, offset: number, str: string) => {
      const prev = str[offset - 1] ?? "";
      const next = str[offset + 1] ?? "";
      return /\d/.test(prev) && /\d/.test(next) ? "." : " ";
    })
    .replace(/§/g, " section ")
    .replace(/[_>|,;:/\\[\]"'“”‘’]/g, " ");
  return spaced.split(/\s+/).filter(Boolean);
}

function normalizeKind(token: string): string | null {
  const bare = token.toLowerCase().replace(/[.\s]+$/, "");
  return KIND_ALIASES[bare] ?? null;
}

/** A token that could be a clause number. Requires a digit so prose
 *  words never become index keys. */
function isNumberish(token: string): boolean {
  return /\d/.test(token) && /^[a-z0-9.()\-]+$/.test(token);
}

function trimNum(token: string): string {
  return token.replace(/^[.\-]+/, "").replace(/[.\-]+$/, "");
}

function pushUnique(keys: string[], key: string): void {
  if (key && !keys.includes(key)) keys.push(key);
}

/** Every label form a stored citation path could be written as inline. */
export function indexKeys(path: string): string[] {
  const tokens = tokenize(path);
  const keys: string[] = [];
  pushUnique(keys, tokens.join(" "));
  for (let i = 0; i < tokens.length; i += 1) {
    const kind = normalizeKind(tokens[i]);
    const next = tokens[i + 1];
    if (kind && next && isNumberish(next)) {
      pushUnique(keys, `${kind} ${trimNum(next)}`);
    }
    if (isNumberish(tokens[i])) {
      pushUnique(keys, trimNum(tokens[i]));
    }
  }
  return keys;
}

/** Lookup keys for an inline reference, most specific first. The
 *  "(a)"-stripped variants let "Section 4.2.1(a)" fall back to a cited
 *  parent clause 4.2.1 rather than going inert. */
export function lookupKeys(kind: string | null, num: string): string[] {
  const n = trimNum(num.toLowerCase());
  const base = n.replace(/\([a-z0-9]+\)$/, "");
  const keys: string[] = [];
  if (kind) pushUnique(keys, `${kind} ${n}`);
  pushUnique(keys, n);
  if (kind && base !== n) pushUnique(keys, `${kind} ${base}`);
  if (base !== n) pushUnique(keys, base);
  return keys;
}

export function buildCitationIndex(
  refs: readonly CitationRef[],
): CitationIndex {
  const byKey = new Map<string, CitationRef>();
  const ambiguous = new Set<string>();
  const distinct = new Set<string>();
  for (const ref of refs) {
    if (!ref.citation) continue;
    distinct.add(ref.citation);
    for (const key of indexKeys(ref.citation)) {
      const existing = byKey.get(key);
      if (existing) {
        // Two different clauses answer to the same label — a link would
        // be a coin flip, so neither gets one.
        if (existing.citation !== ref.citation) ambiguous.add(key);
        continue;
      }
      byKey.set(key, ref);
    }
  }
  return { byKey, ambiguous, count: distinct.size };
}

export const EMPTY_CITATION_INDEX: CitationIndex = buildCitationIndex([]);

export type CitationSpan = {
  start: number;
  end: number;
  text: string;
  kind: string | null;
  num: string;
};

/** Locate every citation-shaped reference in a run of text. Resolution
 *  against the index happens separately — this is purely the scanner. */
export function findCitationSpans(text: string): CitationSpan[] {
  const spans: CitationSpan[] = [];
  const re = new RegExp(CITATION_RE.source, CITATION_RE.flags);
  let match: RegExpExecArray | null;
  while ((match = re.exec(text)) !== null) {
    if (match[0].length === 0) {
      re.lastIndex += 1;
      continue;
    }
    const [full, kindWord, kindNum, symbolNum, bareNum] = match;
    const kind = kindWord ? normalizeKind(kindWord) : null;
    const raw = kindWord ? kindNum : symbolNum || bareNum;
    if (!raw) continue;
    // The number pattern greedily eats a sentence-final period; give it
    // back so the highlighted span stops at the citation itself.
    let num = raw;
    let trimmed = 0;
    while (num.length > 1 && /[.\-]$/.test(num)) {
      num = num.slice(0, -1);
      trimmed += 1;
    }
    const start = match.index;
    const end = start + full.length - trimmed;
    spans.push({
      start,
      end,
      text: text.slice(start, end),
      kind,
      num: num.toLowerCase(),
    });
  }
  return spans;
}

export function resolveCitationSpan(
  index: CitationIndex,
  span: Pick<CitationSpan, "kind" | "num">,
): CitationRef | null {
  for (const key of lookupKeys(span.kind, span.num)) {
    if (index.ambiguous.has(key)) continue;
    const hit = index.byKey.get(key);
    if (hit) return hit;
  }
  return null;
}

export type TextPart =
  | { kind: "text"; text: string }
  | { kind: "citation"; text: string; ref: CitationRef };

/** Split a text run into plain segments and resolved citation segments.
 *  Unresolvable references stay inside the plain segments, so nothing
 *  that can't actually open a clause ever gets link styling. */
export function splitCitations(
  text: string,
  index: CitationIndex,
): TextPart[] {
  if (index.count === 0 || !text) return [{ kind: "text", text }];
  const parts: TextPart[] = [];
  let cursor = 0;
  for (const span of findCitationSpans(text)) {
    if (span.start < cursor) continue;
    const ref = resolveCitationSpan(index, span);
    if (!ref) continue;
    if (span.start > cursor) {
      parts.push({ kind: "text", text: text.slice(cursor, span.start) });
    }
    parts.push({ kind: "citation", text: span.text, ref });
    cursor = span.end;
  }
  if (parts.length === 0) return [{ kind: "text", text }];
  if (cursor < text.length) {
    parts.push({ kind: "text", text: text.slice(cursor) });
  }
  return parts;
}
