// Changelog · Coverage · Support
// All three share the Page + PageHead chrome from pages.jsx.

// ────────────────────────────────────────────────────────────
// CHANGELOG
// Visual concept: numbered like a bylaw amendment register.
// Sticky left rail of version stamps; right column is the entries.

const CHANGELOG_DATA = [
  {
    v: '0.6.0',
    date: '2026-05-14',
    label: 'Reading 2.0',
    pinned: true,
    summary: 'A rewrite of how readings are composed. Citations now show provenance per clause, not per answer.',
    changes: [
      { tag: 'NEW', text: 'Per-clause citation overlays in the chat pane.' },
      { tag: 'NEW', text: 'Conditional outcomes when a parcel triggers more than one zone.' },
      { tag: 'IMPROVED', text: 'Spatial retrieval re-ranks results against parcel boundary, not centroid.' },
      { tag: 'IMPROVED', text: 'Confidence model is now calibrated against 1,140 HRM planner-reviewed answers.' },
      { tag: 'FIXED', text: 'Frontage measurements no longer round before subtracting the corner cutback.' },
    ],
  },
  {
    v: '0.5.4',
    date: '2026-04-30',
    label: 'Mainland LUB consolidation',
    summary: 'Re-indexed the Land Use By-law for Halifax Mainland to the Mar 2026 consolidation.',
    changes: [
      { tag: 'NEW', text: 'Mar 2026 consolidation indexed. Old version still queryable as a historical snapshot.' },
      { tag: 'NEW', text: 'Inline diff between consolidations on any cited section.' },
      { tag: 'IMPROVED', text: 'Table extraction now handles the multi-row dimension cells in Part 9.' },
    ],
  },
  {
    v: '0.5.3',
    date: '2026-04-08',
    label: 'Permit-ready exports',
    summary: 'PDF exports now include a cover sheet, the reasoning trace, and a verifier sign-off field.',
    changes: [
      { tag: 'NEW', text: 'PDF cover sheet with parcel, zone, applicable bylaws.' },
      { tag: 'NEW', text: 'Verifier sign-off block — for a planner or your own QA.' },
      { tag: 'FIXED', text: 'Long answers were being truncated on export at 4 pages.' },
    ],
  },
  {
    v: '0.5.2',
    date: '2026-03-19',
    label: 'Saved parcels',
    summary: 'You can pin a parcel to your sidebar. Readings made against a pinned parcel get grouped together.',
    changes: [
      { tag: 'NEW', text: 'Pin parcels to the sidebar.' },
      { tag: 'NEW', text: 'Parcel-scoped reading history.' },
      { tag: 'IMPROVED', text: 'Reading thread URLs are now permalinks — copy and share within your team.' },
    ],
  },
  {
    v: '0.5.1',
    date: '2026-02-28',
    label: 'Faster cold reads',
    summary: 'First reading on a parcel is 2.4× faster on a warm cache, 1.6× faster cold.',
    changes: [
      { tag: 'IMPROVED', text: 'Retrieval shards now keyed on zone, not just document.' },
      { tag: 'IMPROVED', text: 'Reduced LLM round-trips in the planner step from 3 to 2.' },
      { tag: 'FIXED', text: 'Edge case where parcels straddling two zones returned only one zone\u2019s rules.' },
    ],
  },
  {
    v: '0.5.0',
    date: '2026-02-04',
    label: 'Private beta open',
    summary: 'First invites went out to architects and homeowners in HRM.',
    changes: [
      { tag: 'NEW', text: 'Invite-only signup live.' },
      { tag: 'NEW', text: 'Practice plan with team workspaces.' },
      { tag: 'NEW', text: 'HRM Land Use By-law (Mainland + Peninsula) indexed.' },
    ],
  },
];

const TagPill = ({ tag }) => {
  const t = useTheme();
  const palette = {
    NEW: { bg: t.accent, fg: t.onAccent, border: t.accent },
    IMPROVED: { bg: 'transparent', fg: t.text, border: t.text },
    FIXED: { bg: 'transparent', fg: t.textMuted, border: t.hair },
  };
  const p = palette[tag] || palette.IMPROVED;
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
      padding: '2px 6px', minWidth: 64, textAlign: 'center',
      fontFamily: 'JetBrains Mono', fontSize: 9, letterSpacing: '0.14em',
      background: p.bg, color: p.fg, border: `1px solid ${p.border}`,
    }}>{tag}</span>
  );
};

const ChangelogPage = ({ setRoute }) => {
  const t = useTheme();
  return (
    <Page>
      <PageHead
        kicker="REGISTER · CHANGELOG"
        title="What's changed."
        sub="An amendment register for ABS itself. Every release lists what we added, fixed, and re-indexed against HRM's by-laws."
      />

      <div style={{ display: 'grid', gridTemplateColumns: '220px 1fr', gap: 48, alignItems: 'flex-start' }}>
        {/* Sticky version rail */}
        <aside style={{ position: 'sticky', top: 92, display: 'flex', flexDirection: 'column', gap: 0, borderLeft: `1px solid ${t.hair}` }}>
          <Mono muted size={10} style={{ padding: '0 0 12px 14px' }}>VERSIONS · {CHANGELOG_DATA.length}</Mono>
          {CHANGELOG_DATA.map((r, i) => (
            <a key={r.v} href={`#changelog-${r.v}`} style={{
              display: 'flex', justifyContent: 'space-between', alignItems: 'baseline',
              padding: '10px 14px', textDecoration: 'none',
              borderLeft: `2px solid ${i === 0 ? t.accent : 'transparent'}`, marginLeft: -1,
              color: t.text,
            }}>
              <span style={{ fontFamily: 'JetBrains Mono', fontSize: 12, fontWeight: i === 0 ? 600 : 400, letterSpacing: '0.02em' }}>v{r.v}</span>
              <span style={{ fontFamily: 'JetBrains Mono', fontSize: 9.5, color: t.textMuted, letterSpacing: '0.06em' }}>{r.date.slice(5)}</span>
            </a>
          ))}
          <div style={{ padding: '14px', marginTop: 8, background: t.surfaceAlt, fontSize: 12, color: t.textMuted, lineHeight: 1.45 }}>
            Want every release in your inbox? <a href="#signup" onClick={(e) => { e.preventDefault(); setRoute('signup'); }} style={{ color: t.text }}>Subscribe</a>.
          </div>
        </aside>

        {/* Entries */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 36 }}>
          {CHANGELOG_DATA.map((r, i) => (
            <article id={`changelog-${r.v}`} key={r.v} style={{
              background: r.pinned ? t.surfaceAlt : 'transparent',
              border: r.pinned ? `1.5px solid ${t.text}` : `1px solid ${t.hair}`,
              padding: r.pinned ? '28px 28px 24px' : '22px 24px',
              display: 'flex', flexDirection: 'column', gap: 18, position: 'relative',
            }}>
              {r.pinned && (
                <div style={{ position: 'absolute', top: -1, right: -1, background: t.accent, color: t.onAccent, padding: '4px 10px', fontFamily: 'JetBrains Mono', fontSize: 9.5, letterSpacing: '0.14em' }}>
                  LATEST
                </div>
              )}

              <header style={{ display: 'flex', flexDirection: 'column', gap: 8, paddingBottom: 16, borderBottom: `1px solid ${t.hair}` }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 12, flexWrap: 'wrap' }}>
                  <div style={{ display: 'flex', alignItems: 'baseline', gap: 14 }}>
                    <span style={{ fontFamily: 'JetBrains Mono', fontSize: 14, fontWeight: 600, color: t.accentInk, letterSpacing: '0.04em' }}>v{r.v}</span>
                    <h2 style={{ fontSize: 32, fontWeight: 700, letterSpacing: '-0.03em', lineHeight: 1.05, margin: 0 }}>{r.label}</h2>
                  </div>
                  <Mono muted size={10}>RELEASED · {r.date}</Mono>
                </div>
                <p style={{ fontSize: 14.5, lineHeight: 1.5, color: t.textMuted, margin: 0, maxWidth: 640 }}>{r.summary}</p>
              </header>

              <ul style={{ listStyle: 'none', padding: 0, margin: 0, display: 'flex', flexDirection: 'column', gap: 8 }}>
                {r.changes.map((c, j) => (
                  <li key={j} style={{ display: 'grid', gridTemplateColumns: '76px 1fr', gap: 14, alignItems: 'flex-start', fontSize: 13.5, lineHeight: 1.5 }}>
                    <TagPill tag={c.tag} />
                    <span>{c.text}</span>
                  </li>
                ))}
              </ul>
            </article>
          ))}

          <div style={{ padding: '20px 22px', border: `1px dashed ${t.hair}`, fontFamily: 'JetBrains Mono', fontSize: 11, letterSpacing: '0.04em', color: t.textMuted, textAlign: 'center' }}>
            Pre-0.5.0 history kept internally during closed alpha. Reach out if you need it.
          </div>
        </div>
      </div>
    </Page>
  );
};

// ────────────────────────────────────────────────────────────
// COVERAGE
// Visual concept: an inventory ledger. Big focus on HRM (what's indexed),
// then a roadmap of upcoming jurisdictions with target dates.

const COVERAGE_DATA = {
  active: {
    name: 'Halifax Regional Municipality',
    province: 'Nova Scotia',
    parcels: '38,420',
    documents: 6,
    lastSync: '2026-05-12',
    bylaws: [
      { id: 'LUB-MAINLAND', name: 'Land Use By-law for Halifax Mainland', version: 'Consolidated · Mar 2026', pages: 248, fragments: '4,210', status: 'CURRENT', spatial: true },
      { id: 'LUB-PENINSULA', name: 'Land Use By-law for Halifax Peninsula', version: 'Consolidated · Mar 2026', pages: 214, fragments: '3,884', status: 'CURRENT', spatial: true },
      { id: 'LUB-DARTMOUTH', name: 'Land Use By-law for Dartmouth', version: 'Consolidated · Feb 2026', pages: 196, fragments: '3,402', status: 'CURRENT', spatial: true },
      { id: 'CDD', name: 'Centre Plan — Package A', version: 'Adopted · 2021, am. Jan 2026', pages: 312, fragments: '5,108', status: 'CURRENT', spatial: true },
      { id: 'SUB-BYL', name: 'Regional Subdivision By-law', version: 'Consolidated · Nov 2025', pages: 88, fragments: '1,124', status: 'CURRENT', spatial: false },
      { id: 'NS-BC', name: 'NS Building Code references', version: 'Edition 2020', pages: 36, fragments: '414', status: 'REFERENCE', spatial: false },
    ],
  },
  roadmap: [
    { name: 'Charlottetown', province: 'PE', eta: 'Q3 2026', stage: 'NEGOTIATING', note: 'Bylaw acquisition in progress.' },
    { name: 'Moncton', province: 'NB', eta: 'Q3 2026', stage: 'INDEXING', note: 'Documents acquired, parsing under review.' },
    { name: 'Saint John', province: 'NB', eta: 'Q4 2026', stage: 'INDEXING', note: 'Documents acquired.' },
    { name: 'Fredericton', province: 'NB', eta: 'Q4 2026', stage: 'BACKLOG', note: 'Sequenced after Moncton.' },
    { name: 'St. John\u2019s', province: 'NL', eta: 'Q1 2027', stage: 'BACKLOG', note: 'Targeted for Atlantic rollout.' },
    { name: 'Sydney (CBRM)', province: 'NS', eta: 'Q1 2027', stage: 'BACKLOG', note: 'Following the Atlantic rollout.' },
  ],
};

const CoveragePage = ({ setRoute }) => {
  const t = useTheme();
  const d = COVERAGE_DATA;
  return (
    <Page>
      <PageHead
        kicker="COVERAGE · MAY 2026"
        title="One jurisdiction. Deep."
        sub="ABS is built for one place at a time. We index every applicable bylaw end-to-end before opening a new region — so a reading isn't half-true."
      />

      {/* Active jurisdiction — hero card */}
      <section style={{ background: t.text, color: t.surface, padding: '32px 32px 28px', marginBottom: 14 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 24, flexWrap: 'wrap' }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6, flex: '1 1 360px' }}>
            <Mono size={10} style={{ color: 'rgba(255,255,255,0.55)' }}>ACTIVE · {d.active.province.toUpperCase()}</Mono>
            <h2 style={{ fontSize: 56, fontWeight: 800, letterSpacing: '-0.04em', lineHeight: 0.95, margin: 0 }}>{d.active.name}</h2>
          </div>
          <span style={{ background: t.accent, color: t.onAccent, padding: '5px 11px', fontFamily: 'JetBrains Mono', fontSize: 9.5, letterSpacing: '0.14em', alignSelf: 'flex-start' }}>FULLY INDEXED</span>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 24, marginTop: 28, paddingTop: 22, borderTop: '1px solid rgba(255,255,255,0.15)' }}>
          {[
            { l: 'PARCELS', n: d.active.parcels },
            { l: 'BYLAW DOCUMENTS', n: d.active.documents },
            { l: 'TOTAL FRAGMENTS', n: '18,142' },
            { l: 'LAST SYNC', n: d.active.lastSync },
          ].map(s => (
            <div key={s.l}>
              <Mono size={9.5} style={{ color: 'rgba(255,255,255,0.55)' }}>{s.l}</Mono>
              <div style={{ fontSize: 28, fontWeight: 700, letterSpacing: '-0.025em', marginTop: 6 }}>{s.n}</div>
            </div>
          ))}
        </div>
      </section>

      {/* Bylaws table */}
      <section style={{ border: `1px solid ${t.hair}`, marginBottom: 56 }}>
        <div style={{ padding: '12px 18px', background: t.surfaceAlt, borderBottom: `1px solid ${t.hair}`, display: 'grid', gridTemplateColumns: '2.4fr 1.6fr 0.8fr 0.8fr 0.7fr 0.6fr', gap: 16 }}>
          {['DOCUMENT', 'VERSION', 'PAGES', 'FRAGMENTS', 'SPATIAL', 'STATUS'].map(h => <Mono muted size={9.5} key={h}>{h}</Mono>)}
        </div>
        {d.active.bylaws.map((b, i) => (
          <div key={b.id} style={{
            display: 'grid', gridTemplateColumns: '2.4fr 1.6fr 0.8fr 0.8fr 0.7fr 0.6fr', gap: 16,
            padding: '14px 18px', borderBottom: i < d.active.bylaws.length - 1 ? `1px solid ${t.hair}` : 'none',
            alignItems: 'center', fontSize: 13.5,
          }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
              <span style={{ fontWeight: 600, letterSpacing: '-0.01em' }}>{b.name}</span>
              <Mono muted size={9}>{b.id}</Mono>
            </div>
            <span style={{ color: t.textMuted, fontSize: 12.5 }}>{b.version}</span>
            <span style={{ fontFamily: 'JetBrains Mono', fontSize: 12 }}>{b.pages}</span>
            <span style={{ fontFamily: 'JetBrains Mono', fontSize: 12 }}>{b.fragments}</span>
            <span style={{ fontFamily: 'JetBrains Mono', fontSize: 11, color: b.spatial ? t.accentInk : t.textMuted, letterSpacing: '0.06em' }}>{b.spatial ? '✓ YES' : '— NO'}</span>
            <Mono accent={b.status === 'CURRENT'} muted={b.status !== 'CURRENT'} size={9.5}>{b.status}</Mono>
          </div>
        ))}
      </section>

      {/* Methodology + Roadmap split */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1.2fr', gap: 56, alignItems: 'flex-start' }}>
        {/* Methodology */}
        <section>
          <Mono muted size={10} style={{ marginBottom: 12, display: 'block' }}>HOW WE COVER A JURISDICTION</Mono>
          <h3 style={{ fontSize: 32, fontWeight: 700, letterSpacing: '-0.03em', lineHeight: 1.05, margin: '0 0 22px' }}>
            We don't claim coverage<br/>until it's <HighlightWord>verifiable</HighlightWord>.
          </h3>
          <ol style={{ listStyle: 'none', padding: 0, margin: 0, display: 'flex', flexDirection: 'column', gap: 18 }}>
            {[
              { n: '01', t: 'Acquire', d: 'Source the consolidated bylaw text directly from the municipality.' },
              { n: '02', t: 'Parse', d: 'Extract structure — parts, sections, tables, defined terms — into a fragment graph.' },
              { n: '03', t: 'Link to land', d: 'Join the zoning layer to parcel geometry. Validate against a sample of 50 known parcels.' },
              { n: '04', t: 'Calibrate', d: 'Run 1,000+ planner-reviewed test questions. Coverage launches only above 0.90 calibrated confidence.' },
            ].map(s => (
              <li key={s.n} style={{ display: 'grid', gridTemplateColumns: '50px 1fr', gap: 14, alignItems: 'flex-start' }}>
                <Mono accent size={11}>{s.n}</Mono>
                <div>
                  <div style={{ fontSize: 16, fontWeight: 600, letterSpacing: '-0.015em', marginBottom: 4 }}>{s.t}</div>
                  <div style={{ fontSize: 13.5, color: t.textMuted, lineHeight: 1.5 }}>{s.d}</div>
                </div>
              </li>
            ))}
          </ol>
        </section>

        {/* Roadmap */}
        <section>
          <Mono muted size={10} style={{ marginBottom: 12, display: 'block' }}>ROADMAP · ATLANTIC CANADA</Mono>
          <h3 style={{ fontSize: 32, fontWeight: 700, letterSpacing: '-0.03em', lineHeight: 1.05, margin: '0 0 22px' }}>What's next.</h3>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {d.roadmap.map(r => {
              const stageColor = r.stage === 'INDEXING' ? t.accentInk : r.stage === 'NEGOTIATING' ? t.brick : t.textMuted;
              return (
                <div key={r.name} style={{
                  display: 'grid', gridTemplateColumns: '1.4fr 0.7fr 1fr', gap: 16, alignItems: 'center',
                  padding: '14px 16px', border: `1px solid ${t.hair}`, background: t.surfaceAlt,
                }}>
                  <div>
                    <div style={{ fontSize: 16, fontWeight: 600, letterSpacing: '-0.015em' }}>{r.name}</div>
                    <Mono muted size={9.5}>{r.province}</Mono>
                  </div>
                  <Mono muted size={10}>ETA · {r.eta}</Mono>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, justifyContent: 'flex-end' }}>
                    <span style={{ fontFamily: 'JetBrains Mono', fontSize: 9.5, letterSpacing: '0.12em', color: stageColor }}>{r.stage}</span>
                    <span style={{ width: 8, height: 8, background: stageColor, opacity: r.stage === 'BACKLOG' ? 0.35 : 1 }} />
                  </div>
                </div>
              );
            })}
          </div>
          <div style={{ marginTop: 20, padding: '14px 16px', background: t.text, color: t.surface, display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 16 }}>
            <div>
              <div style={{ fontSize: 15, fontWeight: 600 }}>Want ABS in your city?</div>
              <div style={{ fontSize: 12.5, color: 'rgba(255,255,255,0.7)', marginTop: 2 }}>Tell us. We weight the roadmap by demand.</div>
            </div>
            <Btn variant="accent" size="sm" onClick={() => setRoute('support')}>Request →</Btn>
          </div>
        </section>
      </div>
    </Page>
  );
};

// ────────────────────────────────────────────────────────────
// SUPPORT
// Visual concept: a service desk. Search up top, status indicator,
// category cards, top articles, then contact options.

const SUPPORT_CATEGORIES = [
  {
    icon: '◐',
    name: 'Readings',
    sub: 'How the agent answers',
    articles: ['What counts as a reading?', 'Why does ABS sometimes refuse to answer?', 'Understanding confidence scores'],
  },
  {
    icon: '◧',
    name: 'Parcels & maps',
    sub: 'Address resolution & zoning',
    articles: ['My address resolved to the wrong parcel', 'Parcels straddling two zones', 'What is the difference between LUB and Centre Plan?'],
  },
  {
    icon: '◨',
    name: 'Export & sharing',
    sub: 'Permit-ready exports',
    articles: ['Exporting a reading to PDF', 'Sharing a thread with your team', 'Adding a verifier sign-off'],
  },
  {
    icon: '◓',
    name: 'Account & billing',
    sub: 'Seats, invoices, plans',
    articles: ['Adding a seat to your workspace', 'Switching from Drafter to Practice', 'Updating your billing email'],
  },
];

const SUPPORT_POPULAR = [
  { title: 'How accurate is ABS, really?', mins: '4 min read', tag: 'METHOD' },
  { title: 'What ABS is not: this is research, not legal advice', mins: '2 min read', tag: 'POLICY' },
  { title: 'My answer cites a section that doesn\u2019t apply — what now?', mins: '3 min read', tag: 'READINGS' },
  { title: 'Subletting an answer to a development officer', mins: '5 min read', tag: 'EXPORT' },
];

const SupportPage = ({ setRoute }) => {
  const t = useTheme();
  const [q, setQ] = React.useState('');
  return (
    <Page>
      <PageHead
        kicker="SUPPORT · HRM PRIVATE BETA"
        title="How can we help?"
        sub="Most things you'll want are below. If not, a human on the team replies within one business day during beta."
      />

      {/* Search + status */}
      <div style={{ display: 'grid', gridTemplateColumns: '1.6fr 1fr', gap: 14, marginBottom: 40 }}>
        <form onSubmit={e => e.preventDefault()} style={{ display: 'flex', border: `1.5px solid ${t.text}`, background: t.surface }}>
          <span style={{ padding: '0 14px', display: 'flex', alignItems: 'center', fontFamily: 'JetBrains Mono', fontSize: 12, color: t.textMuted, letterSpacing: '0.08em' }}>SEARCH ›</span>
          <input value={q} onChange={e => setQ(e.target.value)} placeholder="e.g. backyard suite, frontage, export to PDF" style={{
            flex: 1, padding: '14px 0', border: 'none', background: 'transparent', color: t.text,
            fontFamily: 'inherit', fontSize: 15, outline: 'none', letterSpacing: '-0.005em',
          }} />
          <button type="submit" style={{
            background: t.text, color: t.surface, border: 'none', padding: '0 22px',
            fontFamily: 'inherit', fontWeight: 700, fontSize: 14, cursor: 'pointer',
          }}>Search →</button>
        </form>

        <div style={{ border: `1px solid ${t.hair}`, background: t.surfaceAlt, padding: '12px 16px', display: 'flex', alignItems: 'center', gap: 14 }}>
          <span className="abs-pulse-dot" style={{ width: 10, height: 10, background: t.accent, borderRadius: '50%', flexShrink: 0 }} />
          <div style={{ flex: 1 }}>
            <Mono muted size={9.5}>SYSTEM STATUS</Mono>
            <div style={{ fontSize: 14, fontWeight: 600, marginTop: 2 }}>All systems operational</div>
          </div>
          <Mono muted size={9.5}>247 ms · p95</Mono>
        </div>
      </div>

      {/* Categories */}
      <Mono muted size={10} style={{ marginBottom: 14, display: 'block' }}>BROWSE · 4 AREAS</Mono>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 14, marginBottom: 56 }}>
        {SUPPORT_CATEGORIES.map(c => (
          <div key={c.name} style={{
            background: t.surface, border: `1.5px solid ${t.text}`, padding: '24px 26px',
            display: 'flex', flexDirection: 'column', gap: 14,
          }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
              <div>
                <div style={{ fontSize: 26, fontWeight: 700, letterSpacing: '-0.025em', lineHeight: 1.1 }}>{c.name}</div>
                <div style={{ fontSize: 13, color: t.textMuted, marginTop: 4 }}>{c.sub}</div>
              </div>
              <span style={{ fontSize: 36, lineHeight: 1, color: t.accentInk, fontFamily: 'JetBrains Mono' }}>{c.icon}</span>
            </div>
            <ul style={{ listStyle: 'none', padding: 0, margin: 0, display: 'flex', flexDirection: 'column', gap: 0, borderTop: `1px solid ${t.hair}` }}>
              {c.articles.map(a => (
                <li key={a} style={{ padding: '10px 0', borderBottom: `1px solid ${t.hair}`, display: 'flex', justifyContent: 'space-between', alignItems: 'center', cursor: 'pointer', fontSize: 13.5 }}>
                  <span>{a}</span>
                  <span style={{ color: t.textMuted }}>→</span>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>

      {/* Popular */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 56, alignItems: 'flex-start' }}>
        <section>
          <Mono muted size={10} style={{ marginBottom: 14, display: 'block' }}>POPULAR ARTICLES</Mono>
          <h3 style={{ fontSize: 32, fontWeight: 700, letterSpacing: '-0.03em', lineHeight: 1.05, margin: '0 0 22px' }}>What people read first.</h3>
          <ul style={{ listStyle: 'none', padding: 0, margin: 0, display: 'flex', flexDirection: 'column', gap: 0 }}>
            {SUPPORT_POPULAR.map((a, i) => (
              <li key={i} style={{ display: 'grid', gridTemplateColumns: '40px 1fr auto', gap: 16, alignItems: 'center', padding: '16px 0', borderTop: `1px solid ${t.hair}`, cursor: 'pointer' }}>
                <Mono muted size={11}>{String(i + 1).padStart(2, '0')}</Mono>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                  <span style={{ fontSize: 15.5, fontWeight: 600, letterSpacing: '-0.015em' }}>{a.title}</span>
                  <div style={{ display: 'flex', gap: 10 }}>
                    <Mono muted size={9.5}>{a.tag}</Mono>
                    <Mono muted size={9.5}>· {a.mins.toUpperCase()}</Mono>
                  </div>
                </div>
                <span style={{ color: t.textMuted }}>→</span>
              </li>
            ))}
          </ul>
        </section>

        {/* Contact */}
        <section style={{ background: t.surfaceAlt, border: `1px solid ${t.hair}`, padding: 28, display: 'flex', flexDirection: 'column', gap: 22 }}>
          <Mono muted size={10}>STILL STUCK?</Mono>
          <h3 style={{ fontSize: 28, fontWeight: 700, letterSpacing: '-0.025em', lineHeight: 1.1, margin: 0 }}>Talk to a human.</h3>
          <p style={{ fontSize: 14, color: t.textMuted, lineHeight: 1.5, margin: 0 }}>
            During private beta we read every message. Average first reply: 3 hours during HRM business hours.
          </p>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            <a href="mailto:hello@abs.app" style={{ textDecoration: 'none', color: t.text }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '14px 16px', background: t.surface, border: `1px solid ${t.hair}` }}>
                <div>
                  <Mono muted size={9.5}>EMAIL</Mono>
                  <div style={{ fontSize: 14, fontWeight: 600, marginTop: 2 }}>hello@abs.app</div>
                </div>
                <span>→</span>
              </div>
            </a>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '14px 16px', background: t.surface, border: `1px solid ${t.hair}`, cursor: 'pointer' }}>
              <div>
                <Mono muted size={9.5}>IN-APP CHAT</Mono>
                <div style={{ fontSize: 14, fontWeight: 600, marginTop: 2 }}>Open chat in ABS</div>
              </div>
              <Mono accent size={10}>ONLINE</Mono>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '14px 16px', background: t.surface, border: `1px solid ${t.hair}`, cursor: 'pointer' }}>
              <div>
                <Mono muted size={9.5}>OFFICE HOURS</Mono>
                <div style={{ fontSize: 14, fontWeight: 600, marginTop: 2 }}>Thursdays, 11am AT</div>
              </div>
              <span style={{ color: t.textMuted }}>→</span>
            </div>
          </div>

          <div style={{ paddingTop: 14, borderTop: `1px solid ${t.hair}`, fontSize: 12, color: t.textMuted, lineHeight: 1.5 }}>
            Reporting a bad reading? Use the ⚑ flag in the reading itself — it routes to engineering with full context.
          </div>
        </section>
      </div>
    </Page>
  );
};

Object.assign(window, { ChangelogPage, CoveragePage, SupportPage });
