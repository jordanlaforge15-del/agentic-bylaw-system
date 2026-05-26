// ABS App — primary product screen for logged-in users.
// Three panes: conversation list (left), chat thread (center), parcel context (right).
// The chat is the product. Reasoning + citations are inline in the assistant's
// answers, with the parcel pane following along.

const SAMPLE_THREADS = [
  { id: 't1', addr: '5184 Morris St', zone: 'ER-1', title: 'Backyard suite feasibility', preview: 'Yes — up to 80 m².', updated: '2m ago', active: true, unread: false },
  { id: 't2', addr: '1208 Robie St', zone: 'COR', title: 'Maximum height + bonusing', preview: '20 m by-right. Up to 26 m with…', updated: '1h ago', unread: false },
  { id: 't3', addr: '17 Edward St', zone: 'ER-2', title: 'Subdivision check', preview: 'No — frontage 1.4 m short.', updated: 'Yesterday', unread: true },
  { id: 't4', addr: '2310 Gottingen St', zone: 'DH-1', title: 'Ground-floor commercial', preview: 'Permitted as primary use.', updated: '2d ago' },
  { id: 't5', addr: '46 Crichton Ave', zone: 'DR', title: 'Side yard minimums', preview: '1.2 m minimum on both sides.', updated: '3d ago' },
  { id: 't6', addr: '101 Quinpool Rd', zone: 'COR', title: 'Parking minimums', preview: 'None — within transit zone.', updated: '5d ago' },
];

const SAMPLE_MESSAGES = [
  { kind: 'system', body: 'Reading 5184 Morris St · ER-1 (Established Residential, Type 1).' },
  { kind: 'user', body: "Can my client add a backyard suite to this lot? They want a 1-bed unit, around 75 m²." },
  {
    kind: 'agent',
    answer: 'Yes — up to 80 m².',
    body: "A backyard suite is permitted as a secondary use in ER-1. Your client's intended size (75 m²) falls under the 80 m² gross-floor-area cap. Two things to confirm before drawings: rear yard depth and habitable separation from the main dwelling.",
    reasoning: [
      { n: '01', cite: '§ 9.4.1', body: 'Backyard suites are permitted in ER-1 zones as secondary uses to a single-unit dwelling.' },
      { n: '02', cite: '§ 9.4.3', body: 'Maximum gross floor area: 80 m². The 75 m² target clears this with 5 m² of margin.' },
      { n: '03', cite: '§ 9.4.5', body: 'Minimum 1.5 m separation from the principal dwelling. Your site survey shows 4.2 m available — clears.' },
      { n: '04', cite: '§ 5.4', body: 'Rear yard minimum 4.5 m. Verify against most recent survey before final massing.' },
    ],
    confidence: 0.94,
    sources: [
      { title: 'HRM Land Use By-law', section: '§ 9.4 — Backyard Suites', date: '2025-11-04' },
      { title: 'HRM Land Use By-law', section: '§ 5.4 — Yard Requirements', date: '2025-11-04' },
    ],
  },
  { kind: 'user', body: 'What about height? Two storeys?' },
  {
    kind: 'agent',
    answer: 'One storey, max 4.5 m.',
    body: 'ER-1 backyard suites are limited to a single storey with a maximum height of 4.5 m measured from average grade. A two-storey suite would not be by-right — your client would need to apply for a development variance.',
    reasoning: [
      { n: '01', cite: '§ 9.4.4', body: 'Backyard suites in ER-1 are limited to one storey.' },
      { n: '02', cite: '§ 9.4.4', body: 'Maximum height 4.5 m from average grade.' },
      { n: '03', cite: '§ 2.8', body: 'Variances follow the standard development variance process — typically 6–10 weeks.' },
    ],
    confidence: 0.97,
    sources: [
      { title: 'HRM Land Use By-law', section: '§ 9.4 — Backyard Suites', date: '2025-11-04' },
    ],
  },
];

const SUGGESTED = [
  'What does the yard look like?',
  'Generate a massing study',
  'What permits will I need?',
  'Compare to RT-2 limits',
];

const Sidebar = ({ onNew }) => {
  const t = useTheme();
  const [q, setQ] = React.useState('');
  const filtered = SAMPLE_THREADS.filter(x => !q || (x.addr + x.title).toLowerCase().includes(q.toLowerCase()));
  return (
    <aside style={{ width: 280, borderRight: `1px solid ${t.hair}`, background: t.surface, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
      <div style={{ padding: '14px 16px', borderBottom: `1px solid ${t.hair}`, display: 'flex', flexDirection: 'column', gap: 12 }}>
        <Btn variant="primary" size="sm" onClick={onNew} style={{ width: '100%' }}>+ New reading</Btn>
        <input value={q} onChange={e => setQ(e.target.value)} placeholder="Search readings…" style={{
          padding: '8px 10px', background: t.surfaceAlt, color: t.text, border: `1px solid ${t.hair}`,
          fontFamily: 'inherit', fontSize: 12.5, outline: 'none',
        }} />
      </div>
      <div style={{ padding: '12px 16px 6px' }}>
        <Mono muted size={9.5}>RECENT · {filtered.length}</Mono>
      </div>
      <div style={{ flex: 1, overflowY: 'auto', padding: '0 8px 12px', display: 'flex', flexDirection: 'column', gap: 2 }}>
        {filtered.map(th => (
          <button key={th.id} style={{
            background: th.active ? t.surfaceAlt : 'transparent', border: 'none',
            borderLeft: th.active ? `2px solid ${t.accent}` : '2px solid transparent',
            padding: '10px 10px 10px 12px', cursor: 'pointer', textAlign: 'left', display: 'flex', flexDirection: 'column', gap: 4,
            color: t.text, fontFamily: 'inherit',
          }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 8 }}>
              <span style={{ fontSize: 12.5, fontWeight: 600, letterSpacing: '-0.005em' }}>{th.addr}</span>
              {th.unread && <span style={{ width: 6, height: 6, background: t.accent, borderRadius: '50%' }} />}
            </div>
            <span style={{ fontSize: 11.5, color: t.textMuted, lineHeight: 1.35 }}>{th.title}</span>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginTop: 2 }}>
              <Mono muted size={9}>{th.zone}</Mono>
              <Mono muted size={9}>{th.updated.toUpperCase()}</Mono>
            </div>
          </button>
        ))}
      </div>
      <div style={{ padding: '12px 16px', borderTop: `1px solid ${t.hair}`, display: 'flex', alignItems: 'center', gap: 10 }}>
        <div style={{ width: 28, height: 28, background: t.text, color: t.surface, display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: 'JetBrains Mono', fontSize: 11, fontWeight: 600 }}>HS</div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 12.5, fontWeight: 600 }}>Halifax Studio</div>
          <div style={{ fontSize: 10.5, color: t.textMuted }}>Practice · 4 seats</div>
        </div>
        <button style={{ background: 'transparent', border: 'none', color: t.textMuted, cursor: 'pointer', fontFamily: 'JetBrains Mono', fontSize: 11 }}>⚙</button>
      </div>
    </aside>
  );
};

const UserMsg = ({ body }) => {
  const t = useTheme();
  return (
    <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 4 }}>
      <div style={{ maxWidth: '78%', background: t.text, color: t.surface, padding: '12px 16px', fontSize: 14, lineHeight: 1.5 }}>{body}</div>
    </div>
  );
};

const SystemMsg = ({ body }) => {
  const t = useTheme();
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '6px 0', color: t.textMuted, fontFamily: 'JetBrains Mono', fontSize: 11, letterSpacing: '0.04em' }}>
      <div style={{ flex: 1, height: 1, background: t.hair }} />
      <span>{body}</span>
      <div style={{ flex: 1, height: 1, background: t.hair }} />
    </div>
  );
};

const AgentMsg = ({ msg, idx }) => {
  const t = useTheme();
  const [open, setOpen] = React.useState(idx === 0);
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12, marginBottom: 4 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <div style={{ width: 22, height: 22, background: t.accent, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <span style={{ fontFamily: 'Inter Tight', fontWeight: 800, fontSize: 11, color: t.onAccent, letterSpacing: '-0.04em' }}>a</span>
        </div>
        <Mono muted size={10}>ABS · AGENT</Mono>
        <span style={{ flex: 1 }} />
        <Mono accent size={10}>{(msg.confidence * 100).toFixed(0)}% CONF</Mono>
      </div>

      <div style={{ paddingLeft: 32, display: 'flex', flexDirection: 'column', gap: 14 }}>
        <div style={{ fontSize: 24, fontWeight: 800, letterSpacing: '-0.03em', lineHeight: 1.1 }}>
          <HighlightWord>{msg.answer}</HighlightWord>
        </div>
        <div style={{ fontSize: 14.5, lineHeight: 1.55, color: t.text, maxWidth: 640 }}>{msg.body}</div>

        <button onClick={() => setOpen(o => !o)} style={{
          background: 'transparent', border: `1px solid ${t.hair}`, color: t.textMuted,
          padding: '7px 11px', cursor: 'pointer', fontFamily: 'JetBrains Mono', fontSize: 10.5,
          letterSpacing: '0.08em', textTransform: 'uppercase', alignSelf: 'flex-start',
          display: 'inline-flex', alignItems: 'center', gap: 8,
        }}>
          <span>{open ? '▾' : '▸'}</span>
          <span>{msg.reasoning.length} reasoning steps</span>
        </button>

        {open && (
          <div style={{ border: `1px solid ${t.hair}`, background: t.surfaceAlt }}>
            {msg.reasoning.map((r, i) => (
              <div key={r.n} style={{ display: 'grid', gridTemplateColumns: '36px 76px 1fr', padding: '12px 14px', borderBottom: i < msg.reasoning.length - 1 ? `1px solid ${t.hair}` : 'none', alignItems: 'baseline', gap: 14 }}>
                <Mono muted size={10}>{r.n}</Mono>
                <Mono accent size={11} style={{ fontWeight: 600 }}>{r.cite}</Mono>
                <span style={{ fontSize: 13, lineHeight: 1.5 }}>{r.body}</span>
              </div>
            ))}
          </div>
        )}

        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {msg.sources.map((s, i) => (
            <span key={i} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: '4px 8px', border: `1px solid ${t.hair}`, fontFamily: 'JetBrains Mono', fontSize: 10, color: t.textMuted, letterSpacing: '0.04em' }}>
              <span style={{ width: 4, height: 4, background: t.accent }} />
              {s.section}
            </span>
          ))}
        </div>

        <div style={{ display: 'flex', gap: 8, marginTop: 2 }}>
          {['Copy', 'Cite', 'Export', 'Helpful', 'Off'].map(a => (
            <button key={a} style={{
              background: 'transparent', border: 'none', color: t.textMuted,
              padding: '4px 0 4px', cursor: 'pointer', fontFamily: 'JetBrains Mono', fontSize: 10,
              letterSpacing: '0.1em', textTransform: 'uppercase', marginRight: 8,
            }}>{a}</button>
          ))}
        </div>
      </div>
    </div>
  );
};

const ThinkingMsg = ({ steps, step }) => {
  const t = useTheme();
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12, marginBottom: 4 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <div style={{ width: 22, height: 22, background: t.accent, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <span style={{ fontFamily: 'Inter Tight', fontWeight: 800, fontSize: 11, color: t.onAccent, letterSpacing: '-0.04em' }}>a</span>
        </div>
        <Mono muted size={10}>ABS · READING</Mono>
        <span className="abs-pulse-dot" style={{ width: 6, height: 6, background: t.accent, borderRadius: '50%', animation: 'absPulse 1s ease-in-out infinite' }} />
      </div>
      <div style={{ paddingLeft: 32, display: 'flex', flexDirection: 'column', gap: 6 }}>
        {steps.slice(0, step + 1).map((s, i) => (
          <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 10, fontFamily: 'JetBrains Mono', fontSize: 12, color: i === step ? t.text : t.textMuted, letterSpacing: '0.02em' }}>
            <span style={{ color: t.accentInk }}>{i === step ? '→' : '✓'}</span>
            <span>{s}</span>
          </div>
        ))}
      </div>
      <style>{`@keyframes absPulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.3; } }`}</style>
    </div>
  );
};

const ChatThread = ({ messages, thinking, thinkSteps, thinkStep }) => {
  const t = useTheme();
  const ref = React.useRef(null);
  React.useEffect(() => { if (ref.current) ref.current.scrollTop = ref.current.scrollHeight; }, [messages.length, thinking, thinkStep]);
  return (
    <div ref={ref} style={{ flex: 1, overflowY: 'auto', padding: '24px 36px', display: 'flex', flexDirection: 'column', gap: 18 }}>
      {messages.map((m, i) => {
        if (m.kind === 'system') return <SystemMsg key={i} body={m.body} />;
        if (m.kind === 'user') return <UserMsg key={i} body={m.body} />;
        return <AgentMsg key={i} msg={m} idx={i} />;
      })}
      {thinking && <ThinkingMsg steps={thinkSteps} step={thinkStep} />}
    </div>
  );
};

const Composer = ({ onSend, disabled }) => {
  const t = useTheme();
  const [val, setVal] = React.useState('');
  const submit = (e) => { e.preventDefault(); if (val.trim() && !disabled) { onSend(val); setVal(''); } };
  return (
    <div style={{ borderTop: `1px solid ${t.hair}`, padding: '14px 36px 18px', background: t.surface }}>
      <div style={{ display: 'flex', gap: 6, marginBottom: 10, flexWrap: 'wrap' }}>
        {SUGGESTED.map(s => (
          <button key={s} onClick={() => onSend(s)} disabled={disabled} style={{
            background: t.surfaceAlt, border: `1px solid ${t.hair}`, color: t.text,
            padding: '6px 10px', cursor: 'pointer', fontFamily: 'inherit', fontSize: 12,
            letterSpacing: '-0.005em',
          }}>{s}</button>
        ))}
      </div>
      <form onSubmit={submit} style={{ display: 'flex', gap: 0, border: `1.5px solid ${t.text}`, background: t.surface }}>
        <textarea
          value={val}
          onChange={e => setVal(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) submit(e); }}
          placeholder="Ask about this parcel — yard, height, use, density…"
          rows={1}
          disabled={disabled}
          style={{
            flex: 1, padding: '12px 14px', border: 'none', resize: 'none',
            background: 'transparent', color: t.text, fontFamily: 'inherit', fontSize: 14,
            outline: 'none', letterSpacing: '-0.005em', minHeight: 44,
          }}
        />
        <button type="submit" disabled={disabled || !val.trim()} style={{
          background: t.text, color: t.surface, border: 'none', padding: '0 22px',
          fontFamily: 'inherit', fontWeight: 700, fontSize: 14, letterSpacing: '-0.01em',
          cursor: disabled ? 'not-allowed' : 'pointer', opacity: disabled || !val.trim() ? 0.5 : 1,
        }}>Send →</button>
      </form>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 8 }}>
        <Mono muted size={9.5}>ENTER TO SEND · SHIFT+ENTER FOR NEWLINE</Mono>
        <Mono muted size={9.5}>NOT LEGAL ADVICE · VERIFY WITH HRM PLANNING</Mono>
      </div>
    </div>
  );
};

const ParcelPane = () => {
  const t = useTheme();
  return (
    <aside style={{ width: 340, borderLeft: `1px solid ${t.hair}`, background: t.surfaceAlt, display: 'flex', flexDirection: 'column', minHeight: 0, overflow: 'auto' }}>
      <div style={{ padding: '16px 20px', borderBottom: `1px solid ${t.hair}`, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <Mono muted size={10}>PARCEL</Mono>
        <button style={{ background: 'transparent', border: `1px solid ${t.hair}`, color: t.text, padding: '4px 8px', fontFamily: 'JetBrains Mono', fontSize: 9.5, letterSpacing: '0.1em', cursor: 'pointer' }}>CHANGE</button>
      </div>

      <div style={{ padding: '18px 20px', display: 'flex', flexDirection: 'column', gap: 6 }}>
        <div style={{ fontSize: 22, fontWeight: 700, letterSpacing: '-0.025em', lineHeight: 1.15 }}>5184 Morris St</div>
        <div style={{ fontSize: 12.5, color: t.textMuted }}>Halifax, NS · B3J 1B5</div>
      </div>

      <div style={{ padding: '0 20px 18px' }}>
        <div style={{ aspectRatio: '4 / 3', background: t.surface, border: `1px solid ${t.hair}`, position: 'relative', overflow: 'hidden' }}>
          <svg viewBox="0 0 200 150" style={{ width: '100%', height: '100%' }}>
            <rect x="20" y="20" width="160" height="110" fill="none" stroke={t.text} strokeWidth="0.6" />
            <rect x="36" y="36" width="128" height="78" fill={t.text} fillOpacity="0.04" stroke={t.text} strokeWidth="0.4" strokeDasharray="2 2" />
            <rect x="56" y="48" width="60" height="40" fill={t.text} fillOpacity="0.08" stroke={t.text} strokeWidth="0.6" />
            <rect x="120" y="68" width="34" height="34" fill={t.accent} fillOpacity="0.5" stroke={t.accent} strokeWidth="0.8" />
            <text x="137" y="88" fontSize="5" fill={t.text} fontFamily="JetBrains Mono" textAnchor="middle">SUITE</text>
            <line x1="20" y1="14" x2="180" y2="14" stroke={t.accent} strokeWidth="0.6" />
            <text x="100" y="11" fontSize="4.5" fill={t.text} fontFamily="JetBrains Mono" textAnchor="middle">11.4 m</text>
          </svg>
          <div style={{ position: 'absolute', bottom: 6, right: 8, fontFamily: 'JetBrains Mono', fontSize: 8.5, letterSpacing: '0.14em', color: t.textMuted }}>SITE · 1:200</div>
        </div>
      </div>

      <div style={{ padding: '0 20px 18px', display: 'flex', flexDirection: 'column', gap: 8 }}>
        {[
          ['Lot area', '372 m²'],
          ['Frontage', '11.4 m'],
          ['Zoning', 'ER-1'],
          ['Existing units', '1 (1924)'],
          ['Heritage', 'No'],
          ['Within transit zone', 'Yes'],
        ].map(([k, v]) => (
          <div key={k} style={{ display: 'flex', justifyContent: 'space-between', borderBottom: `1px dotted ${t.hair}`, paddingBottom: 6, fontSize: 12 }}>
            <span style={{ color: t.textMuted, fontFamily: 'JetBrains Mono', letterSpacing: '0.04em' }}>{k}</span>
            <span style={{ fontWeight: 600 }}>{v}</span>
          </div>
        ))}
      </div>

      <div style={{ padding: '12px 20px', borderTop: `1px solid ${t.hair}`, display: 'flex', flexDirection: 'column', gap: 10 }}>
        <Mono muted size={10}>CITED THIS THREAD · 3</Mono>
        {[
          { c: '§ 9.4', n: 'Backyard Suites', d: '2025-11-04' },
          { c: '§ 5.4', n: 'Yard Requirements', d: '2025-11-04' },
          { c: '§ 2.8', n: 'Variances', d: '2025-11-04' },
        ].map(s => (
          <div key={s.c} style={{ background: t.surface, border: `1px solid ${t.hair}`, padding: '10px 12px', display: 'flex', flexDirection: 'column', gap: 4 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
              <Mono accent size={11} style={{ fontWeight: 600 }}>{s.c}</Mono>
              <Mono muted size={9}>{s.d}</Mono>
            </div>
            <span style={{ fontSize: 12.5 }}>{s.n}</span>
          </div>
        ))}
      </div>

      <div style={{ marginTop: 'auto', padding: '14px 20px', borderTop: `1px solid ${t.hair}`, display: 'flex', flexDirection: 'column', gap: 8 }}>
        <Btn variant="primary" size="sm" style={{ width: '100%' }}>Export reading (PDF)</Btn>
        <Btn variant="ghost" size="sm" style={{ width: '100%' }}>Share with team</Btn>
      </div>
    </aside>
  );
};

const AppHeader = ({ setRoute, mode, setMode }) => {
  const t = useTheme();
  const isDark = mode === 'dark';
  return (
    <div style={{ padding: '12px 20px', borderBottom: `1px solid ${t.hair}`, display: 'flex', alignItems: 'center', justifyContent: 'space-between', background: t.surface }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
        <button onClick={() => setRoute('home')} style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}><ABSLogo size={20} /></button>
        <span style={{ width: 1, height: 16, background: t.hair }} />
        <Mono muted size={10}>READING · 5184 MORRIS ST · ER-1</Mono>
        <span style={{ width: 6, height: 6, background: t.accent, borderRadius: '50%' }} />
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <Mono muted size={10}>VERIFIED 2026·05·06</Mono>
        {setMode && (
          <button onClick={() => setMode(isDark ? 'light' : 'dark')} style={{
            display: 'inline-flex', alignItems: 'center', background: t.surfaceAlt, border: `1px solid ${t.hair}`,
            padding: 2, cursor: 'pointer', fontFamily: 'JetBrains Mono', fontSize: 9.5,
            letterSpacing: '0.12em', textTransform: 'uppercase',
          }}>
            <span style={{ padding: '4px 8px', background: isDark ? 'transparent' : t.text, color: isDark ? t.textMuted : t.surface }}>04</span>
            <span style={{ padding: '4px 8px', background: isDark ? t.text : 'transparent', color: isDark ? t.surface : t.textMuted }}>03</span>
          </button>
        )}
        <Btn variant="quiet" size="sm" onClick={() => setRoute('billing')}>Account</Btn>
      </div>
    </div>
  );
};

const AppPage = ({ setRoute, mode, setMode }) => {
  const t = useTheme();
  const [messages, setMessages] = React.useState(SAMPLE_MESSAGES);
  const [thinking, setThinking] = React.useState(false);
  const [thinkStep, setThinkStep] = React.useState(0);
  const STEPS = [
    'Locating parcel in HRM cadastre…',
    'Loading Land Use By-law (rev. 2025-11-04)…',
    'Reading § 9.4 — Backyard Suites…',
    'Cross-checking § 5.4 yard requirements…',
    'Compiling answer…',
  ];

  const send = (text) => {
    setMessages(prev => [...prev, { kind: 'user', body: text }]);
    setThinking(true);
    setThinkStep(0);
    let i = 0;
    const tick = () => {
      i += 1;
      if (i >= STEPS.length) {
        setMessages(prev => [...prev, {
          kind: 'agent',
          answer: 'Likely yes — with conditions.',
          body: "Based on the parcel's frontage and yard depths, your follow-up sits within standard ER-1 thresholds. The key contingency is the principal-dwelling separation — please attach a current site survey before final design.",
          reasoning: [
            { n: '01', cite: '§ 9.4.5', body: 'Principal-dwelling separation: 1.5 m minimum. Survey required.' },
            { n: '02', cite: '§ 5.4', body: 'Yard depths within ER-1 standard ranges based on cadastral data.' },
            { n: '03', cite: '§ 2.8', body: 'Conditional approvals available via standard variance.' },
          ],
          confidence: 0.88,
          sources: [{ title: 'HRM Land Use By-law', section: '§ 9.4', date: '2025-11-04' }],
        }]);
        setThinking(false);
      } else {
        setThinkStep(i);
        setTimeout(tick, 520 + Math.random() * 240);
      }
    };
    setTimeout(tick, 380);
  };

  return (
    <div style={{ height: '100vh', display: 'flex', flexDirection: 'column', background: t.surface, color: t.text, overflow: 'hidden' }}>
      <AppHeader setRoute={setRoute} mode={mode} setMode={setMode} />
      <div style={{ flex: 1, display: 'flex', minHeight: 0 }}>
        <Sidebar onNew={() => setMessages([{ kind: 'system', body: 'New reading — paste an HRM address to begin.' }])} />
        <main style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0, background: t.surface }}>
          <ChatThread messages={messages} thinking={thinking} thinkSteps={STEPS} thinkStep={thinkStep} />
          <Composer onSend={send} disabled={thinking} />
        </main>
        <ParcelPane />
      </div>
    </div>
  );
};

window.AppPage = AppPage;
