// #newcase — "Open a case"
// New product model: opening a case is free; you pay per answer. The user
// anchors to a property, picks one priced report type, describes the
// situation, then buys the answer. Rebuilt in the ABS° blueprint system.

const ANCHOR_TYPES = ['Property address', 'Parcel ID (PID)', 'Civic address'];

const CASE_QUESTIONS = [
  {
    id: 'permitted-use',
    name: 'Permitted-use check',
    price: 79,
    desc: 'Is a specific use allowed at a specific address? We resolve the address to its zone and report whether the use is permitted, permitted-as-of-right, conditional, or prohibited, with the governing by-law provisions.',
  },
  {
    id: 'dev-standards',
    name: 'Development-standards compliance check',
    price: 149,
    desc: "Does a proposed project comply with the as-of-right development standards of its zone — height, setbacks, lot coverage, floor-area ratio, and parking? We evaluate the supplied dimensions against the zone's standards and flag every non-conformity.",
  },
  {
    id: 'due-diligence',
    name: 'Zoning due-diligence summary',
    price: 199,
    desc: 'A preliminary zoning due-diligence summary for an address: the zone, permitted and conditional uses, applicable overlays, and flags for any apparent non-conformity. Anchors a purchase or financing decision; not a substitute for a full consultant memo.',
  },
  {
    id: 'non-conforming',
    name: 'Legal non-conforming determination',
    price: 199,
    desc: 'Is an existing use or structure legally non-conforming (i.e. lawfully established before the current by-law and protected), or simply non-compliant? We evaluate the situation against the current standards and the non-conforming provisions.',
  },
  {
    id: 'variance',
    name: 'Variance justification package',
    price: 299,
    desc: 'A justification package for a requested variance, addressing the three statutory criteria (the variance is minor, the strict application creates undue hardship, and the variance is desirable and consistent with the intent of the by-law and plan).',
  },
];

const CaseRadio = ({ selected }) => {
  const t = useTheme();
  return (
    <span style={{
      width: 16, height: 16, flexShrink: 0, marginTop: 1, display: 'inline-flex',
      alignItems: 'center', justifyContent: 'center',
      border: `1.5px solid ${selected ? t.accent : t.rule}`,
      background: selected ? t.accent : 'transparent', color: t.onAccent,
      fontFamily: 'JetBrains Mono', fontSize: 10, lineHeight: 1, transition: 'all .12s',
    }}>{selected ? '✓' : ''}</span>
  );
};

const QuestionCard = ({ q, selected, onSelect }) => {
  const t = useTheme();
  const [hover, setHover] = React.useState(false);
  return (
    <button
      onClick={onSelect}
      onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}
      style={{
        textAlign: 'left', cursor: 'pointer', fontFamily: 'inherit', color: t.text,
        background: selected ? t.surfaceAlt : t.surface,
        border: `1.5px solid ${selected || hover ? t.text : t.hair}`,
        borderLeft: `${selected ? 3 : 1.5}px solid ${selected ? t.accent : (hover ? t.text : t.hair)}`,
        padding: '20px 22px', display: 'flex', flexDirection: 'column', gap: 10,
        transition: 'border-color .12s, background .12s',
      }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 16 }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 11 }}>
          <CaseRadio selected={selected} />
          <span style={{ fontSize: 16, fontWeight: 700, letterSpacing: '-0.02em', lineHeight: 1.18 }}>{q.name}</span>
        </div>
        <div style={{ textAlign: 'right', flexShrink: 0 }}>
          <div style={{ fontSize: 21, fontWeight: 800, letterSpacing: '-0.03em', lineHeight: 1 }}>{'$' + q.price}</div>
          <Mono muted size={8.5} style={{ display: 'block', marginTop: 3 }}>PER ANSWER</Mono>
        </div>
      </div>
      <p style={{ fontSize: 13, lineHeight: 1.5, color: t.textMuted, margin: 0, paddingLeft: 27 }}>{q.desc}</p>
    </button>
  );
};

const SectionLabel = ({ children, style }) => (
  <Mono muted size={10} style={{ display: 'block', marginBottom: 14, ...style }}>{children}</Mono>
);

const OpenCasePage = ({ setRoute }) => {
  const t = useTheme();
  const [anchorType, setAnchorType] = React.useState(ANCHOR_TYPES[0]);
  const [address, setAddress] = React.useState('5184 Morris St, Halifax');
  const [selected, setSelected] = React.useState('due-diligence');
  const [situation, setSituation] = React.useState('');

  const q = CASE_QUESTIONS.find(x => x.id === selected);

  return (
    <div style={{ padding: '56px 32px 72px', maxWidth: 1060, margin: '0 auto', minHeight: 'calc(100vh - 200px)' }}>
      <PageHead
        kicker="ACCOUNT · NEW CASE"
        title="Open a case."
        sub="Anchor your inquiry to a property, then pick the question you want answered. Opening a case is free — you pay per answer. We'll reuse an existing case if you opened one for the same anchor in the last 30 days."
      />

      {/* Anchor */}
      <section style={{ marginBottom: 44 }}>
        <SectionLabel>ANCHOR</SectionLabel>
        <div style={{ display: 'flex', border: `1.5px solid ${t.text}`, background: t.surface }}>
          <select value={anchorType} onChange={e => setAnchorType(e.target.value)} style={{
            border: 'none', borderRight: `1px solid ${t.hair}`, background: t.surfaceAlt, color: t.text,
            fontFamily: 'inherit', fontSize: 14, padding: '13px 14px', outline: 'none', cursor: 'pointer',
            letterSpacing: '-0.005em', flexShrink: 0,
          }}>
            {ANCHOR_TYPES.map(o => <option key={o} value={o}>{o}</option>)}
          </select>
          <input
            value={address} onChange={e => setAddress(e.target.value)}
            placeholder="e.g. 1234 Main St, Halifax"
            style={{
              flex: 1, border: 'none', background: 'transparent', color: t.text,
              fontFamily: 'inherit', fontSize: 14, padding: '13px 16px', outline: 'none', letterSpacing: '-0.005em',
            }} />
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 16, marginTop: 10, flexWrap: 'wrap' }}>
          <span style={{ fontSize: 13, color: t.textMuted, lineHeight: 1.45 }}>
            Opening or continuing a case is free — you only pay when you buy an answer below.
          </span>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 7, border: `1px solid ${t.hair}`, padding: '4px 9px', flexShrink: 0 }}>
            <span style={{ width: 5, height: 5, background: t.accent }} />
            <Mono muted size={9}>REUSES AN OPEN CASE · 30 DAYS</Mono>
          </span>
        </div>
      </section>

      {/* Choose a question */}
      <section style={{ marginBottom: 44 }}>
        <SectionLabel>CHOOSE A QUESTION · {CASE_QUESTIONS.length}</SectionLabel>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
          {CASE_QUESTIONS.map(item => (
            <QuestionCard key={item.id} q={item} selected={selected === item.id} onSelect={() => setSelected(item.id)} />
          ))}
        </div>
      </section>

      {/* Describe */}
      <section style={{ marginBottom: 40 }}>
        <SectionLabel>DESCRIBE YOUR SITUATION</SectionLabel>
        <textarea
          value={situation} onChange={e => setSituation(e.target.value)}
          placeholder="Tell us what you want answered. We'll ask for anything else we need before you pay."
          rows={5}
          style={{
            width: '100%', boxSizing: 'border-box', border: `1.5px solid ${t.text}`, background: t.surface,
            color: t.text, fontFamily: 'inherit', fontSize: 14, padding: '14px 16px', outline: 'none',
            resize: 'vertical', lineHeight: 1.55, letterSpacing: '-0.005em', minHeight: 132,
          }} />
      </section>

      {/* Checkout */}
      <section style={{ borderTop: `1px solid ${t.rule}`, paddingTop: 22, display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end', gap: 28, flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 5, minWidth: 240 }}>
          <Mono muted size={9.5}>YOUR ANSWER</Mono>
          <div style={{ fontSize: 18, fontWeight: 700, letterSpacing: '-0.02em' }}>{q.name}</div>
          <Mono muted size={9.5}>{(address ? address.toUpperCase() : 'NO ADDRESS YET')} · {anchorType.toUpperCase()}</Mono>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 22 }}>
          <div style={{ textAlign: 'right' }}>
            <div style={{ fontSize: 28, fontWeight: 800, letterSpacing: '-0.035em', lineHeight: 1 }}>{'$' + q.price}</div>
            <Mono accent size={9} style={{ display: 'block', marginTop: 4 }}>FIRST ANSWER FREE · BETA</Mono>
          </div>
          <Btn variant="accent" size="lg" onClick={() => setRoute('app')}>Get answer (free trial) →</Btn>
        </div>
      </section>

      <Mono muted size={9} style={{ display: 'block', marginTop: 22 }}>
        NOT LEGAL ADVICE · RESEARCH ONLY · VERIFY WITH HRM PLANNING
      </Mono>
    </div>
  );
};

Object.assign(window, { OpenCasePage, CASE_QUESTIONS });
