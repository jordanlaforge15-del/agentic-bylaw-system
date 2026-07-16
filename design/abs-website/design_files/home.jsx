// Home page. Hero with live agent walkthrough + working address input.
// Tweak: bold | safe — bold uses massive display & full-bleed accent; safe is
// quieter and more product-screenshot oriented.

const SAMPLE_READINGS = [
  {
    addr: '5184 Morris St',
    zone: 'ER-1',
    q: 'Can I add a backyard suite?',
    verdict: 'Yes — up to 80 m².',
    cite: '§ 9.4',
  },
  {
    addr: '1208 Robie St',
    zone: 'COR',
    q: 'How tall can I build?',
    verdict: '20 m by-right. Up to 26 m with a bonus.',
    cite: '§ 6.2.3',
  },
  {
    addr: '17 Edward St',
    zone: 'ER-2',
    q: 'Can I subdivide the lot?',
    verdict: 'No — frontage is 1.4 m short.',
    cite: '§ 4.3',
  },
];

// — Working address input that returns a fake reading
const AddressDemo = () => {
  const t = useTheme();
  const [val, setVal] = React.useState('');
  const [state, setState] = React.useState('idle'); // idle | thinking | done
  const [reading, setReading] = React.useState(null);
  const [step, setStep] = React.useState(0);

  const STEPS = [
    'Geocoding parcel…',
    'Fetching HRM Land Use By-law…',
    'Reading § 9 — Established Residential…',
    'Cross-checking § 4.3 frontage minimums…',
    'Compiling answer…',
  ];

  const submit = (presetAddr) => {
    const a = (presetAddr || val).trim();
    if (!a) return;
    setState('thinking');
    setStep(0);
    const r = SAMPLE_READINGS.find(s => a.toLowerCase().includes(s.addr.toLowerCase().slice(0, 5))) || SAMPLE_READINGS[0];
    let i = 0;
    const tick = () => {
      i += 1;
      if (i >= STEPS.length) {
        setReading({ ...r, addr: a });
        setState('done');
      } else {
        setStep(i);
        setTimeout(tick, 480 + Math.random() * 200);
      }
    };
    setTimeout(tick, 380);
  };

  const reset = () => { setState('idle'); setReading(null); setVal(''); setStep(0); };

  return (
    <div style={{ background: t.surfaceAlt, border: `1.5px solid ${t.text}`, padding: 22, display: 'flex', flexDirection: 'column', gap: 14, minHeight: 320 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <Mono muted size={10}>TRY IT · HRM ADDRESSES</Mono>
        {state !== 'idle' && <button onClick={reset} style={{ background: 'transparent', border: 'none', color: t.textMuted, cursor: 'pointer', fontFamily: 'JetBrains Mono', fontSize: 10, letterSpacing: '0.12em' }}>RESET ↻</button>}
      </div>

      {state === 'idle' && (
        <>
          <form onSubmit={e => { e.preventDefault(); submit(); }} style={{ display: 'flex', gap: 0, border: `1.5px solid ${t.text}` }}>
            <input value={val} onChange={e => setVal(e.target.value)} placeholder="e.g. 5184 Morris St, Halifax" style={{
              flex: 1, padding: '14px 16px', border: 'none', background: t.surface, color: t.text,
              fontFamily: 'inherit', fontSize: 15, outline: 'none', letterSpacing: '-0.005em',
            }} />
            <button type="submit" style={{
              background: t.text, color: t.surface, border: 'none', padding: '0 20px',
              fontFamily: 'inherit', fontWeight: 700, fontSize: 14, letterSpacing: '-0.01em', cursor: 'pointer',
            }}>Read it →</button>
          </form>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 4 }}>
            <span style={{ fontSize: 11.5, color: t.textMuted, marginRight: 4, alignSelf: 'center' }}>or try:</span>
            {SAMPLE_READINGS.map(s => (
              <button key={s.addr} onClick={() => submit(s.addr)} style={{
                background: 'transparent', border: `1px solid ${t.hair}`, padding: '5px 9px',
                fontFamily: 'JetBrains Mono', fontSize: 10.5, letterSpacing: '0.04em', color: t.text,
                cursor: 'pointer',
              }}>{s.addr}</button>
            ))}
          </div>
        </>
      )}

      {state === 'thinking' && (
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 8, paddingTop: 4 }}>
          <Mono accent size={11}>READING · {Math.round((step / STEPS.length) * 100)}%</Mono>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {STEPS.slice(0, step + 1).map((s, i) => (
              <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 10, fontFamily: 'JetBrains Mono', fontSize: 12, color: i === step ? t.text : t.textMuted, letterSpacing: '0.02em' }}>
                <span style={{ color: t.accentInk }}>{i === step ? '→' : '✓'}</span>
                <span>{s}</span>
                {i === step && <span className="abs-pulse-dot" style={{ width: 6, height: 6, background: t.accent, marginLeft: 4 }} />}
              </div>
            ))}
          </div>
          <div style={{ flex: 1 }} />
          <div style={{ height: 3, background: t.hair, position: 'relative', overflow: 'hidden' }}>
            <div style={{ position: 'absolute', left: 0, top: 0, bottom: 0, background: t.accent, width: `${((step + 1) / STEPS.length) * 100}%`, transition: 'width 0.4s ease' }} />
          </div>
        </div>
      )}

      {state === 'done' && reading && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14, paddingTop: 4 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
            <Mono muted size={10}>{reading.addr.toUpperCase()} · {reading.zone}</Mono>
            <Mono accent size={10}>VERIFIED · 0.93 CONF</Mono>
          </div>
          <div style={{ fontSize: 14, color: t.textMuted, fontStyle: 'italic' }}>{reading.q}</div>
          <div style={{ fontSize: 32, fontWeight: 800, letterSpacing: '-0.035em', lineHeight: 1.1 }}>
            <HighlightWord>{reading.verdict}</HighlightWord>
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', paddingTop: 8, borderTop: `1px solid ${t.hair}` }}>
            <Mono muted size={10}>SOURCE · HRM LUB {reading.cite}</Mono>
            <Btn variant="ghost" size="sm">Open full reading →</Btn>
          </div>
        </div>
      )}

      <style>{`
        .abs-pulse-dot { animation: absPulse 1s ease-in-out infinite; border-radius: 50%; }
        @keyframes absPulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.3; } }
      `}</style>
    </div>
  );
};

// — Animated agent walkthrough (rotating samples)
const AgentWalkthrough = () => {
  const t = useTheme();
  const [idx, setIdx] = React.useState(0);
  const [phase, setPhase] = React.useState('typing'); // typing | reading | answer
  const [typed, setTyped] = React.useState('');
  const sample = SAMPLE_READINGS[idx];

  React.useEffect(() => {
    setTyped(''); setPhase('typing');
    let i = 0;
    const typeIv = setInterval(() => {
      i += 1;
      setTyped(sample.q.slice(0, i));
      if (i >= sample.q.length) { clearInterval(typeIv); setTimeout(() => setPhase('reading'), 400); }
    }, 32);
    return () => clearInterval(typeIv);
  }, [idx]);

  React.useEffect(() => {
    if (phase === 'reading') { const id = setTimeout(() => setPhase('answer'), 1800); return () => clearTimeout(id); }
    if (phase === 'answer') { const id = setTimeout(() => setIdx(i => (i + 1) % SAMPLE_READINGS.length), 3200); return () => clearTimeout(id); }
  }, [phase]);

  return (
    <div style={{ background: t.surfaceAlt, border: `1px solid ${t.hair}`, padding: 0, display: 'flex', flexDirection: 'column', minHeight: 420, overflow: 'hidden' }}>
      <div style={{ padding: '12px 18px', borderBottom: `1px solid ${t.hair}`, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ width: 7, height: 7, background: t.accent, borderRadius: '50%' }} className="abs-pulse-dot" />
          <Mono muted size={10}>ABS AGENT · LIVE</Mono>
        </div>
        <Mono muted size={10}>{sample.addr.toUpperCase()} · {sample.zone}</Mono>
      </div>

      <div style={{ flex: 1, padding: '20px 22px', display: 'flex', flexDirection: 'column', gap: 14 }}>
        {/* user message */}
        <div style={{ alignSelf: 'flex-end', maxWidth: '78%', background: t.text, color: t.surface, padding: '10px 14px', fontSize: 14 }}>
          {typed}{phase === 'typing' && <span className="abs-cursor">▍</span>}
        </div>

        {/* agent thinking */}
        {(phase === 'reading' || phase === 'answer') && (
          <div style={{ alignSelf: 'flex-start', maxWidth: '90%', display: 'flex', flexDirection: 'column', gap: 10 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, fontFamily: 'JetBrains Mono', fontSize: 11.5, color: t.textMuted }}>
              <span style={{ color: t.accentInk }}>→</span>
              <span>Reading HRM LUB {sample.cite}…</span>
              {phase === 'reading' && <span className="abs-pulse-dot" style={{ width: 5, height: 5, background: t.accent }} />}
            </div>
            {phase === 'answer' && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                <div style={{ fontSize: 13.5, lineHeight: 1.5, color: t.text }}>Here's what the bylaw says:</div>
                <div style={{ fontSize: 28, fontWeight: 800, letterSpacing: '-0.035em', lineHeight: 1.1 }} className="abs-fade-in">
                  <HighlightWord>{sample.verdict}</HighlightWord>
                </div>
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 4 }} className="abs-fade-in">
                  <span style={{ fontFamily: 'JetBrains Mono', fontSize: 10, padding: '3px 8px', border: `1px solid ${t.hair}`, color: t.textMuted, letterSpacing: '0.06em' }}>SOURCE · {sample.cite}</span>
                  <span style={{ fontFamily: 'JetBrains Mono', fontSize: 10, padding: '3px 8px', border: `1px solid ${t.hair}`, color: t.textMuted, letterSpacing: '0.06em' }}>0.93 CONF.</span>
                  <span style={{ fontFamily: 'JetBrains Mono', fontSize: 10, padding: '3px 8px', border: `1px solid ${t.hair}`, color: t.textMuted, letterSpacing: '0.06em' }}>VERIFIED 2026·04·30</span>
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      <div style={{ padding: '12px 18px', borderTop: `1px solid ${t.hair}`, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div style={{ display: 'flex', gap: 4 }}>
          {SAMPLE_READINGS.map((_, i) => (
            <span key={i} style={{ width: 14, height: 2, background: i === idx ? t.accent : t.hair, transition: 'background 0.3s' }} />
          ))}
        </div>
        <Mono muted size={9.5}>SAMPLE {idx + 1} / {SAMPLE_READINGS.length}</Mono>
      </div>

      <style>{`
        .abs-cursor { display: inline-block; animation: absBlink 0.9s steps(2) infinite; }
        @keyframes absBlink { 0%, 50% { opacity: 1; } 50.01%, 100% { opacity: 0; } }
        .abs-fade-in { animation: absFade 0.5s ease-out; }
        @keyframes absFade { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: none; } }
      `}</style>
    </div>
  );
};

// — Hero — safe variant
const HeroSafe = ({ setRoute }) => {
  const t = useTheme();
  return (
    <section style={{ padding: '64px 32px 56px', maxWidth: 1340, margin: '0 auto' }}>
      <div style={{ display: 'grid', gridTemplateColumns: '1.05fr 1fr', gap: 56, alignItems: 'center' }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 22 }}>
          <Mono muted size={11}>HRM · PRIVATE BETA · MAY 2026</Mono>
          <h1 style={{ fontSize: 76, fontWeight: 800, letterSpacing: '-0.045em', lineHeight: 0.95, margin: 0 }}>
            An expert<br/>planner, in your<br/><HighlightWord>workflow.</HighlightWord>
          </h1>
          <p style={{ fontSize: 19, lineHeight: 1.4, color: t.textMuted, margin: 0, maxWidth: 520 }}>
            ABS reads the Halifax Regional Municipality Land Use By-law, applied
            to your specific parcel. Ask in plain English. Get a sourced answer
            in seconds.
          </p>
          <div style={{ display: 'flex', gap: 10, marginTop: 6 }}>
            <Btn variant="primary" size="lg" onClick={() => setRoute('signup')}>Get an invite →</Btn>
            <Btn variant="ghost" size="lg" onClick={() => setRoute('pricing')}>See pricing</Btn>
          </div>
          <div style={{ display: 'flex', gap: 18, paddingTop: 14, borderTop: `1px solid ${t.hair}`, marginTop: 12 }}>
            <Stat n="HRM" l="JURISDICTION" />
            <Stat n="38k" l="PARCELS INDEXED" />
            <Stat n="0.94" l="AVG. CONFIDENCE" />
          </div>
        </div>

        <AgentWalkthrough />
      </div>
    </section>
  );
};

// — Hero — bold variant: massive type, full-bleed accent strip, no screenshot above the fold
const HeroBold = ({ setRoute }) => {
  const t = useTheme();
  return (
    <section style={{ position: 'relative', overflow: 'hidden' }}>
      <div style={{ padding: '72px 32px 0', maxWidth: 1500, margin: '0 auto' }}>
        <Mono muted size={11}>HRM · PRIVATE BETA · MAY 2026</Mono>
        <h1 style={{ fontSize: 168, fontWeight: 800, letterSpacing: '-0.06em', lineHeight: 0.86, margin: '18px 0 0', whiteSpace: 'nowrap' }}>
          Read the bylaw.
        </h1>
        <h1 style={{ fontSize: 168, fontWeight: 800, letterSpacing: '-0.06em', lineHeight: 0.86, margin: '0 0 0', whiteSpace: 'nowrap' }}>
          <HighlightWord height={0.16}>Build the thing.</HighlightWord>
        </h1>

        <div style={{ display: 'grid', gridTemplateColumns: '1.4fr 1fr', gap: 56, alignItems: 'flex-end', marginTop: 40 }}>
          <p style={{ fontSize: 22, lineHeight: 1.35, color: t.text, margin: 0, maxWidth: 640, fontWeight: 500 }}>
            ABS is an expert planner integrated into your workflow. Ask in plain
            English. Get a sourced answer in seconds — drawn from the Halifax
            Regional Municipality Land Use By-law, applied to your parcel.
          </p>
          <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
            <Btn variant="primary" size="lg" onClick={() => setRoute('signup')}>Get an invite →</Btn>
            <Btn variant="ghost" size="lg" onClick={() => setRoute('pricing')}>Pricing</Btn>
          </div>
        </div>
      </div>

      <div style={{ background: t.accent, color: t.onAccent, marginTop: 56, padding: '12px 32px', display: 'flex', justifyContent: 'space-between', alignItems: 'center', overflow: 'hidden' }}>
        <Mono size={11} style={{ color: t.onAccent }}>// MAXIMIZE YOUR BUILD</Mono>
        <Mono size={11} style={{ color: t.onAccent }}>HALIFAX · DARTMOUTH · BEDFORD · SACKVILLE · COLE HARBOUR · HAMMONDS PLAINS</Mono>
        <Mono size={11} style={{ color: t.onAccent }}>// PRIVATE BETA</Mono>
      </div>

      <div style={{ padding: '56px 32px', maxWidth: 1500, margin: '0 auto' }}>
        <AgentWalkthrough />
      </div>
    </section>
  );
};

const Stat = ({ n, l }) => {
  const t = useTheme();
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      <span style={{ fontSize: 22, fontWeight: 700, letterSpacing: '-0.025em' }}>{n}</span>
      <Mono muted size={9.5}>{l}</Mono>
    </div>
  );
};

const Section = ({ kicker, title, children, narrow }) => {
  const t = useTheme();
  return (
    <section style={{ padding: '56px 32px', maxWidth: narrow ? 980 : 1340, margin: '0 auto', borderTop: `1px solid ${t.hair}` }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 14, marginBottom: 22 }}>
        <Mono muted size={10}>{kicker}</Mono>
        <div style={{ flex: 1, height: 1, background: t.hair }} />
      </div>
      {title && <h2 style={{ fontSize: 48, fontWeight: 700, letterSpacing: '-0.035em', lineHeight: 1.05, margin: '0 0 28px', maxWidth: 720 }}>{title}</h2>}
      {children}
    </section>
  );
};

const HowItWorks = () => {
  const t = useTheme();
  const steps = [
    { n: '01', t: 'Ask', d: 'Plain English. "Can I add a backyard suite?" "How tall can I build?" Type it like you would to a planner.' },
    { n: '02', t: 'ABS reads', d: 'The agent locates your parcel, opens the relevant sections of the HRM Land Use By-law, and works the math.' },
    { n: '03', t: 'You get a sourced answer', d: 'A verdict, the reasoning, and citations to the exact sections — ready to attach to a permit application.' },
  ];
  return (
    <Section kicker="HOW IT WORKS · 3 STEPS" title="The bylaw, read for you. Sourced and dated.">
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 0, border: `1px solid ${t.hair}` }}>
        {steps.map((s, i) => (
          <div key={s.n} style={{ padding: '28px 24px', borderRight: i < 2 ? `1px solid ${t.hair}` : 'none', display: 'flex', flexDirection: 'column', gap: 14, position: 'relative' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <Mono muted size={11}>STEP · {s.n}</Mono>
              <span style={{ width: 24, height: 4, background: t.accent }} />
            </div>
            <div style={{ fontSize: 28, fontWeight: 700, letterSpacing: '-0.025em', lineHeight: 1.1 }}>{s.t}</div>
            <div style={{ fontSize: 14, lineHeight: 1.5, color: t.textMuted }}>{s.d}</div>
          </div>
        ))}
      </div>
    </Section>
  );
};

const ProofGrid = () => {
  const t = useTheme();
  const PROOF = [
    { addr: '5184 Morris St · ER-1', q: 'Backyard suite?', a: 'Yes — up to 80 m².', cite: 'HRM LUB § 9.4', accent: true },
    { addr: '1208 Robie St · COR', q: 'Max height?', a: '20 m by-right.', cite: 'HRM LUB § 6.2.3' },
    { addr: '17 Edward St · ER-2', q: 'Subdivide?', a: 'No — 1.4 m short.', cite: 'HRM LUB § 4.3' },
    { addr: '2310 Gottingen St · DH-1', q: 'Commercial use?', a: 'Permitted on ground floor.', cite: 'HRM LUB § 7.1' },
    { addr: '46 Crichton Ave · DR', q: 'Side yard?', a: '1.2 m minimum.', cite: 'HRM LUB § 5.4' },
    { addr: '101 Quinpool Rd · COR', q: 'Parking minimum?', a: 'None — within transit zone.', cite: 'HRM LUB § 8.2' },
  ];
  return (
    <Section kicker="REAL READINGS · ANONYMIZED" title="What ABS has answered this week.">
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 14 }}>
        {PROOF.map((p, i) => (
          <div key={i} style={{
            background: p.accent ? t.accent : t.surfaceAlt, color: p.accent ? t.onAccent : t.text,
            border: p.accent ? 'none' : `1px solid ${t.hair}`, padding: 22,
            display: 'flex', flexDirection: 'column', gap: 10, minHeight: 200, justifyContent: 'space-between',
          }}>
            <Mono size={9.5} style={{ color: p.accent ? t.onAccent : t.textMuted }}>{p.addr}</Mono>
            <div>
              <div style={{ fontSize: 13, color: p.accent ? t.onAccent : t.textMuted, marginBottom: 6, fontStyle: 'italic' }}>"{p.q}"</div>
              <div style={{ fontSize: 24, fontWeight: 800, letterSpacing: '-0.03em', lineHeight: 1.15 }}>{p.a}</div>
            </div>
            <Mono size={9.5} style={{ color: p.accent ? t.onAccent : t.textMuted }}>{p.cite}</Mono>
          </div>
        ))}
      </div>
    </Section>
  );
};

const TryDemo = ({ setRoute }) => {
  const t = useTheme();
  return (
    <Section kicker="TRY IT · NO ACCOUNT NEEDED">
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 36, alignItems: 'center' }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <h2 style={{ fontSize: 48, fontWeight: 700, letterSpacing: '-0.035em', lineHeight: 1.05, margin: 0 }}>
            Paste an HRM address.<br/>See what's permitted.
          </h2>
          <p style={{ fontSize: 16, color: t.textMuted, lineHeight: 1.5, margin: 0, maxWidth: 460 }}>
            The full agent runs the same way once you're in. This is a slice —
            one question, one reading, one source.
          </p>
        </div>
        <AddressDemo />
      </div>
    </Section>
  );
};

const ClosingCTA = ({ setRoute }) => {
  const t = useTheme();
  return (
    <Section kicker="JOIN THE BETA">
      <div style={{ background: t.text, color: t.surface, padding: '48px 36px', display: 'grid', gridTemplateColumns: '1.4fr 1fr', gap: 32, alignItems: 'center' }}>
        <div>
          <h2 style={{ fontSize: 56, fontWeight: 800, letterSpacing: '-0.045em', lineHeight: 0.95, margin: 0 }}>
            Maximize<br/>your build.
          </h2>
          <p style={{ fontSize: 16, color: t.textMuted, lineHeight: 1.5, marginTop: 18, maxWidth: 440 }}>
            Currently invite-only while we deepen HRM coverage. Tell us about
            your project and we'll get you in.
          </p>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <Btn variant="accent" size="lg" onClick={() => setRoute('signup')}>Request an invite →</Btn>
          <Btn variant="ghost" size="lg" onClick={() => setRoute('pricing')} style={{ borderColor: t.surface, color: t.surface }}>See pricing</Btn>
        </div>
      </div>
    </Section>
  );
};

const HomePage = ({ setRoute, variant = 'safe' }) => (
  <>
    {variant === 'bold' ? <HeroBold setRoute={setRoute} /> : <HeroSafe setRoute={setRoute} />}
    <HowItWorks />
    <TryDemo setRoute={setRoute} />
    <ProofGrid />
    <ClosingCTA setRoute={setRoute} />
  </>
);

window.HomePage = HomePage;
