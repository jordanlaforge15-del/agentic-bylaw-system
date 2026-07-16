// Right-pane Sources view — renders citation evidence shaped exactly like the
// retrieval MCP search_bylaw_evidence response. See README in handoff for
// schema. Every field that has UX meaning is surfaced.

// — Sample citations for the active thread (backyard-suite question in ER-1).
//   Shape matches search_bylaw_evidence.matches[i] from the MCP.

const CITATIONS_DATA = {
  total_matches: 12,
  shown_matches: 4,
  truncation_note: '8 lower-scored matches not shown. Narrow by section prefix to surface more.',
  document: {
    id: 14,
    municipality: 'Halifax Regional Municipality',
    bylaw_name: 'Land Use By-law for Halifax Mainland',
    version_label: 'Consolidated to Mar 2026',
    consolidation_date: '2026-03-12',
    page_count: 248,
  },
  matches: [
    {
      fragment_id: 71221,
      document_id: 14,
      municipality: 'Halifax Regional Municipality',
      bylaw_name: 'Land Use By-law for Halifax Mainland',
      page_start: 184, page_end: 185,
      text: 'A backyard suite is permitted as a secondary use to a single-unit dwelling in the ER-1 zone, subject to the standards of Section 9.4.3.',
      score: 0.912,
      citation_path: '9.4.1',
      citation_label: '9.4.1 Permitted Use',
      retrieval_channels: ['text', 'spatial'],
      ancestor_chain: [
        { citation_path: '9', citation_label: 'Part 9 — Secondary Uses' },
        { citation_path: '9.4', citation_label: '9.4 Backyard Suites' },
      ],
      cross_references: [
        { resolution_status: 'resolved', target_citation_path: '9.4.3', target_citation_label: '§ 9.4.3' },
      ],
      linked_datasets: [
        {
          dataset_id: 7,
          name: 'halifax_zoning_districts',
          location_resolver: 'google_maps',
          location_confidence: 0.96,
          feature_matches: [
            {
              canonical_attributes: { zone: 'ER-1', sub_district: 'North End', heritage_overlay: 'No' },
              contains_input: true,
            },
          ],
        },
      ],
    },
    {
      fragment_id: 71243,
      document_id: 14,
      municipality: 'Halifax Regional Municipality',
      bylaw_name: 'Land Use By-law for Halifax Mainland',
      page_start: 187, page_end: 188,
      text: 'A backyard suite shall comply with the standards in Table 9.4.3: maximum gross floor area 80 m², maximum height 4.5 m, one storey only.',
      score: 0.847,
      citation_path: '9.4.3',
      citation_label: '9.4.3 Standards',
      retrieval_channels: ['text'],
      ancestor_chain: [
        { citation_path: '9', citation_label: 'Part 9 — Secondary Uses' },
        { citation_path: '9.4', citation_label: '9.4 Backyard Suites' },
      ],
      tables: [
        {
          table_id: 4421,
          page_start: 187, page_end: 187,
          caption: 'Table 9.4.3: Backyard Suite Standards',
          preview: 'Standard | Limit\nMax floor area | 80 m²\nMax height | 4.5 m\nStoreys | 1\nSetback from dwelling | 1.5 m',
        },
      ],
      cross_references: [
        { resolution_status: 'resolved', target_citation_path: '5.4', target_citation_label: '§ 5.4' },
        { resolution_status: 'unresolved', target_citation_guess: 'NS Building Code, Part 9' },
      ],
    },
    {
      fragment_id: 70188,
      document_id: 14,
      municipality: 'Halifax Regional Municipality',
      bylaw_name: 'Land Use By-law for Halifax Mainland',
      page_start: 92, page_end: 93,
      text: 'Rear yard minimum 4.5 m for accessory structures exceeding 10 m² gross floor area.',
      score: 0.681,
      citation_path: '5.4',
      citation_label: '5.4 Yard Requirements',
      retrieval_channels: ['text', 'spatial'],
      ancestor_chain: [
        { citation_path: '5', citation_label: 'Part 5 — General Provisions' },
      ],
      linked_datasets: [
        {
          dataset_id: 12,
          name: 'halifax_parcel_dimensions',
          location_resolver: 'address_interpolation',
          location_confidence: 0.79,
          feature_matches: [
            {
              canonical_attributes: { rear_yard_available_m: 5.7, frontage_m: 11.4 },
              contains_input: true,
            },
          ],
        },
      ],
    },
    {
      fragment_id: 65021,
      document_id: 14,
      municipality: 'Halifax Regional Municipality',
      bylaw_name: 'Land Use By-law for Halifax Mainland',
      page_start: 31, page_end: 31,
      text: 'A development variance may be granted by the Development Officer for relaxations under Section 9.4 up to 10% of the prescribed limit.',
      score: 0.522,
      citation_path: '2.8',
      citation_label: '2.8 Variances',
      retrieval_channels: ['text'],
      ancestor_chain: [
        { citation_path: '2', citation_label: 'Part 2 — Administration' },
      ],
    },
  ],
};

// — Atoms

const ScoreBar = ({ score }) => {
  const t = useTheme();
  const cells = 5;
  const filled = Math.round(score * cells);
  return (
    <div style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }} title={`Relevance ${score.toFixed(2)}`}>
      <div style={{ display: 'flex', gap: 1.5 }}>
        {Array.from({ length: cells }).map((_, i) => (
          <span key={i} style={{
            width: 7, height: 8,
            background: i < filled ? (score >= 0.8 ? t.accent : t.text) : t.hair,
            opacity: i < filled ? 1 : 1,
          }} />
        ))}
      </div>
      <span style={{ fontFamily: 'JetBrains Mono', fontSize: 9.5, letterSpacing: '0.04em', color: t.textMuted }}>
        {score.toFixed(2)}
      </span>
    </div>
  );
};

const ChannelBadge = ({ channel }) => {
  const t = useTheme();
  const isSpatial = channel === 'spatial';
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 4,
      padding: '2px 6px',
      fontFamily: 'JetBrains Mono', fontSize: 9, letterSpacing: '0.12em',
      textTransform: 'uppercase',
      background: isSpatial ? t.accent : 'transparent',
      color: isSpatial ? t.onAccent : t.textMuted,
      border: `1px solid ${isSpatial ? t.accent : t.hair}`,
    }}>{channel}</span>
  );
};

const Breadcrumb = ({ chain }) => {
  const t = useTheme();
  if (!chain || !chain.length) return null;
  return (
    <div style={{
      fontFamily: 'JetBrains Mono', fontSize: 9.5, letterSpacing: '0.04em',
      color: t.textMuted, lineHeight: 1.4,
    }}>
      {chain.map((a, i) => (
        <React.Fragment key={a.citation_path}>
          <span style={{ cursor: 'pointer' }}>{a.citation_label}</span>
          {i < chain.length - 1 && <span style={{ margin: '0 6px', opacity: 0.5 }}>›</span>}
        </React.Fragment>
      ))}
    </div>
  );
};

const LinkedDataset = ({ ds }) => {
  const t = useTheme();
  const fm = ds.feature_matches?.[0];
  const lowConfidence = ds.location_confidence != null && ds.location_confidence < 0.85;
  const hit = !!fm?.contains_input;

  return (
    <div style={{
      background: t.surface,
      borderLeft: `2px solid ${hit ? t.accent : t.hair}`,
      padding: '10px 12px',
      display: 'flex', flexDirection: 'column', gap: 8,
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
        <span style={{ fontFamily: 'JetBrains Mono', fontSize: 9, letterSpacing: '0.12em', textTransform: 'uppercase', color: hit ? t.accentInk : t.textMuted }}>
          {hit ? '⟡ At this parcel' : 'Nearby dataset'}
        </span>
        {lowConfidence && (
          <span style={{
            fontFamily: 'JetBrains Mono', fontSize: 9, letterSpacing: '0.1em', textTransform: 'uppercase',
            padding: '1px 5px', border: `1px solid ${t.brick}`, color: t.brick,
          }} title={`Geocoder confidence ${ds.location_confidence}`}>~ approx loc</span>
        )}
      </div>

      {fm?.canonical_attributes && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          {Object.entries(fm.canonical_attributes).map(([k, v]) => (
            <div key={k} style={{ display: 'flex', justifyContent: 'space-between', gap: 8, fontSize: 11.5, paddingBottom: 3, borderBottom: `1px dotted ${t.hair}` }}>
              <span style={{ fontFamily: 'JetBrains Mono', fontSize: 9.5, letterSpacing: '0.04em', color: t.textMuted }}>{k}</span>
              <span style={{ fontWeight: 600, textAlign: 'right' }}>{String(v)}</span>
            </div>
          ))}
        </div>
      )}

      <div style={{ display: 'flex', justifyContent: 'space-between', fontFamily: 'JetBrains Mono', fontSize: 8.5, letterSpacing: '0.06em', color: t.textMuted }}>
        <span>{ds.name}</span>
        <span>{(ds.location_confidence * 100).toFixed(0)}% · {ds.location_resolver}</span>
      </div>
    </div>
  );
};

const TablePreview = ({ table }) => {
  const t = useTheme();
  const rows = table.preview.split('\n').map(r => r.split('|').map(c => c.trim()));
  const [head, ...body] = rows;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <span style={{ fontFamily: 'JetBrains Mono', fontSize: 9, letterSpacing: '0.12em', textTransform: 'uppercase', color: t.textMuted }}>
        ⎚ {table.caption} · p. {table.page_start}
      </span>
      <div style={{ border: `1px solid ${t.hair}`, background: t.surface, fontSize: 11 }}>
        <div style={{ display: 'grid', gridTemplateColumns: head.map(() => '1fr').join(' '), background: t.surfaceAlt }}>
          {head.map((c, i) => (
            <div key={i} style={{ padding: '6px 8px', fontFamily: 'JetBrains Mono', fontSize: 9, letterSpacing: '0.1em', textTransform: 'uppercase', color: t.textMuted, borderRight: i < head.length - 1 ? `1px solid ${t.hair}` : 'none' }}>{c}</div>
          ))}
        </div>
        {body.map((row, ri) => (
          <div key={ri} style={{ display: 'grid', gridTemplateColumns: head.map(() => '1fr').join(' '), borderTop: `1px solid ${t.hair}` }}>
            {row.map((cell, ci) => (
              <div key={ci} style={{ padding: '6px 8px', borderRight: ci < row.length - 1 ? `1px solid ${t.hair}` : 'none', fontWeight: ci === row.length - 1 ? 600 : 400 }}>{cell}</div>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
};

const CrossRefList = ({ refs }) => {
  const t = useTheme();
  if (!refs?.length) return null;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <span style={{ fontFamily: 'JetBrains Mono', fontSize: 9, letterSpacing: '0.12em', textTransform: 'uppercase', color: t.textMuted }}>See also</span>
      <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap' }}>
        {refs.map((r, i) => {
          const resolved = r.resolution_status === 'resolved';
          return (
            <span key={i} title={resolved ? `Jump to ${r.target_citation_path}` : 'Unresolved — best guess'} style={{
              fontFamily: 'JetBrains Mono', fontSize: 10, letterSpacing: '0.04em',
              padding: '3px 7px',
              background: 'transparent',
              color: resolved ? t.text : t.textMuted,
              border: `1px solid ${resolved ? t.text : t.hair}`,
              borderStyle: resolved ? 'solid' : 'dashed',
              cursor: resolved ? 'pointer' : 'default',
              display: 'inline-flex', alignItems: 'center', gap: 5,
            }}>
              {resolved ? r.target_citation_label : <><span style={{ opacity: 0.6 }}>?</span> {r.target_citation_guess}</>}
            </span>
          );
        })}
      </div>
    </div>
  );
};

// — Per-citation card

const CitationCard = ({ c, defaultOpen }) => {
  const t = useTheme();
  const [open, setOpen] = React.useState(defaultOpen);
  const spatialOnly = c.retrieval_channels?.length === 1 && c.retrieval_channels[0] === 'spatial';

  return (
    <article style={{
      background: t.surface,
      border: `1px solid ${t.hair}`,
      borderLeft: spatialOnly ? `2px solid ${t.accent}` : `1px solid ${t.hair}`,
      display: 'flex', flexDirection: 'column',
    }}>
      {/* Header — always visible */}
      <button onClick={() => setOpen(o => !o)} style={{
        background: 'transparent', border: 'none', padding: '12px 14px',
        cursor: 'pointer', textAlign: 'left', display: 'flex', flexDirection: 'column', gap: 8,
        color: t.text, fontFamily: 'inherit',
      }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 10 }}>
          <span style={{
            fontFamily: 'Inter Tight', fontSize: 14, fontWeight: 700, letterSpacing: '-0.015em',
            color: t.accentInk, lineHeight: 1.2,
          }}>
            §&nbsp;{c.citation_label}
          </span>
          <span style={{ color: t.textMuted, fontSize: 11, lineHeight: 1, paddingTop: 4 }}>{open ? '▾' : '▸'}</span>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <ScoreBar score={c.score} />
          {c.retrieval_channels?.map(ch => <ChannelBadge key={ch} channel={ch} />)}
        </div>

        <Breadcrumb chain={c.ancestor_chain} />
      </button>

      {open && (
        <div style={{ padding: '0 14px 14px', display: 'flex', flexDirection: 'column', gap: 12, borderTop: `1px solid ${t.hair}`, paddingTop: 12 }}>
          {/* Excerpt */}
          <blockquote style={{
            margin: 0, padding: '0 0 0 10px',
            borderLeft: `2px solid ${t.hair}`,
            fontSize: 12.5, lineHeight: 1.5, color: t.text,
          }}>
            "{c.text}"
          </blockquote>

          {/* Source line */}
          <div style={{ display: 'flex', justifyContent: 'space-between', fontFamily: 'JetBrains Mono', fontSize: 9, letterSpacing: '0.08em', color: t.textMuted, textTransform: 'uppercase' }}>
            <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '70%' }}>{c.bylaw_name.replace('Land Use By-law for ', 'LUB · ').replace('Regional Centre Land Use By-law', 'Regional Centre LUB')}</span>
            <span>pp. {c.page_start === c.page_end ? c.page_start : `${c.page_start}–${c.page_end}`}</span>
          </div>

          {/* Linked datasets */}
          {c.linked_datasets?.map(ds => <LinkedDataset key={ds.dataset_id} ds={ds} />)}

          {/* Tables */}
          {c.tables?.map(tbl => <TablePreview key={tbl.table_id} table={tbl} />)}

          {/* Cross-refs */}
          <CrossRefList refs={c.cross_references} />

          {/* Footer action */}
          <div style={{ display: 'flex', gap: 8, paddingTop: 4 }}>
            <button style={{
              background: 'transparent', border: `1px solid ${t.text}`, color: t.text,
              padding: '6px 10px', fontFamily: 'inherit', fontSize: 11.5, fontWeight: 600,
              letterSpacing: '-0.005em', cursor: 'pointer', flex: 1,
            }}>Open in document →</button>
            <button style={{
              background: 'transparent', border: `1px solid ${t.hair}`, color: t.textMuted,
              padding: '6px 10px', fontFamily: 'JetBrains Mono', fontSize: 10,
              letterSpacing: '0.08em', cursor: 'pointer',
            }}>COPY §</button>
          </div>
        </div>
      )}
    </article>
  );
};

// — Pane

const CitationsPane = () => {
  const t = useTheme();
  const data = CITATIONS_DATA;
  const [filterText, setFilterText] = React.useState(false);
  const [prefix, setPrefix] = React.useState('');

  const filtered = prefix
    ? data.matches.filter(m => m.citation_path.startsWith(prefix))
    : data.matches;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', minHeight: 0, height: '100%' }}>
      {/* Thread-level header */}
      <div style={{ padding: '14px 18px', borderBottom: `1px solid ${t.hair}`, display: 'flex', flexDirection: 'column', gap: 10 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
            <span style={{ fontSize: 18, fontWeight: 700, letterSpacing: '-0.02em' }}>Sources</span>
            <span style={{ fontFamily: 'JetBrains Mono', fontSize: 11, color: t.textMuted, letterSpacing: '0.04em' }}>{data.shown_matches} / {data.total_matches}</span>
          </div>
          <button style={{
            background: 'transparent', border: `1px solid ${t.hair}`, color: t.textMuted,
            padding: '3px 7px', fontFamily: 'JetBrains Mono', fontSize: 9.5, letterSpacing: '0.1em',
            cursor: 'pointer',
          }} onClick={() => setFilterText(f => !f)}>FILTER</button>
        </div>

        {/* Document meta */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
          <span style={{ fontSize: 12.5, fontWeight: 600 }}>{data.document.bylaw_name}</span>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontFamily: 'JetBrains Mono', fontSize: 9, letterSpacing: '0.08em', color: t.textMuted, textTransform: 'uppercase' }}>
            <span>{data.document.version_label}</span>
            <span>{data.document.page_count} pp</span>
          </div>
        </div>

        {filterText && (
          <input
            value={prefix}
            onChange={e => setPrefix(e.target.value)}
            placeholder="Section prefix · e.g. 9.4"
            style={{
              padding: '7px 10px', background: t.surface, color: t.text,
              border: `1px solid ${t.text}`, fontFamily: 'JetBrains Mono', fontSize: 11.5,
              outline: 'none', letterSpacing: '0.02em',
            }}
          />
        )}
      </div>

      {/* List */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '14px 14px 8px', display: 'flex', flexDirection: 'column', gap: 10 }}>
        {filtered.map((c, i) => <CitationCard key={c.fragment_id} c={c} defaultOpen={i === 0} />)}

        {filtered.length === 0 && (
          <div style={{ padding: 20, fontSize: 12.5, color: t.textMuted, textAlign: 'center', border: `1px dashed ${t.hair}` }}>
            No matches under <code style={{ fontFamily: 'JetBrains Mono' }}>{prefix}</code>. Try a shorter prefix.
          </div>
        )}

        {data.truncation_note && !prefix && (
          <div style={{
            padding: '10px 12px', background: t.surfaceAlt, border: `1px dashed ${t.hair}`,
            fontSize: 11.5, lineHeight: 1.4, color: t.textMuted,
          }}>
            {data.truncation_note}
          </div>
        )}
      </div>

      {/* Footer */}
      <div style={{ padding: '12px 14px', borderTop: `1px solid ${t.hair}`, display: 'flex', flexDirection: 'column', gap: 8 }}>
        <button style={{
          background: t.text, color: t.surface, border: `1.5px solid ${t.text}`,
          padding: '10px 12px', fontFamily: 'inherit', fontSize: 12.5, fontWeight: 600,
          letterSpacing: '-0.005em', cursor: 'pointer', width: '100%',
        }}>Export sources (PDF)</button>
        <button style={{
          background: 'transparent', color: t.text, border: `1.5px solid ${t.text}`,
          padding: '10px 12px', fontFamily: 'inherit', fontSize: 12.5, fontWeight: 600,
          letterSpacing: '-0.005em', cursor: 'pointer', width: '100%',
        }}>Browse outline</button>
      </div>
    </div>
  );
};

Object.assign(window, { CitationsPane, CITATIONS_DATA });
