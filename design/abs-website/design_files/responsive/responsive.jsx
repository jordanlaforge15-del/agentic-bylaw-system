// ABS° — Responsive variants on a design canvas.
// Mobile = 375, Tablet = 768. Dark theme (Setback) is primary; one light artboard for sanity.

const T = {
  surface: '#0a0a0a', surfaceAlt: '#171614', surfaceInk: '#ede8db',
  text: '#ede8db', textMuted: '#9a9484', rule: 'rgba(237,232,219,0.5)',
  hair: 'rgba(237,232,219,0.12)', accent: '#c9f24c', accentInk: '#c9f24c',
  onAccent: '#0a0a0a',
};
const L = {
  surface: '#ffffff', surfaceAlt: '#f7f6f2', surfaceInk: '#0a0a0a',
  text: '#0a0a0a', textMuted: '#7a7468', rule: 'rgba(10,10,10,0.45)',
  hair: 'rgba(10,10,10,0.1)', accent: '#c9f24c', accentInk: '#5a7a1a',
  onAccent: '#0a0a0a',
};

// — primitives
const Logo = ({ size = 22, t = T }) => (
  <span style={{ fontFamily: 'Inter Tight', fontWeight: 800, fontSize: size, color: t.text, letterSpacing: '-0.06em', lineHeight: 1, display: 'inline-flex', alignItems: 'center' }}>
    <span>abs</span>
    <span style={{ display: 'inline-block', width: size * 0.18, height: size * 0.78, background: t.accent, marginLeft: size * 0.08 }} />
  </span>
);
const Mono = ({ children, t = T, muted, accent, size = 9.5, style }) => (
  <span style={{ fontFamily: 'JetBrains Mono', fontSize: size, letterSpacing: '0.14em', textTransform: 'uppercase', color: accent ? t.accentInk : muted ? t.textMuted : t.text, ...style }}>{children}</span>
);
const HL = ({ children, t = T }) => (
  <span style={{ position: 'relative', display: 'inline-block' }}>
    {children}
    <span style={{ position: 'absolute', left: 0, right: 0, bottom: '14%', height: '0.18em', background: t.accent, zIndex: -1 }} />
  </span>
);
const Hair = ({ t = T, vertical, style }) => (
  <div style={{ background: t.hair, [vertical ? 'width' : 'height']: 1, [vertical ? 'height' : 'width']: '100%', ...style }} />
);
const Btn = ({ children, t = T, variant = 'primary', size = 'md', style, ...rest }) => {
  const sizes = { sm: { padding: '8px 12px', fontSize: 12 }, md: { padding: '11px 16px', fontSize: 13 }, lg: { padding: '13px 18px', fontSize: 14 } };
  const variants = {
    primary: { background: t.text, color: t.surface, border: `1.5px solid ${t.text}` },
    accent: { background: t.accent, color: t.onAccent, border: `1.5px solid ${t.accent}` },
    ghost: { background: 'transparent', color: t.text, border: `1.5px solid ${t.text}` },
    quiet: { background: 'transparent', color: t.textMuted, border: `1.5px solid ${t.hair}` },
  };
  return <button {...rest} style={{ ...variants[variant], ...sizes[size], fontFamily: 'inherit', fontWeight: 600, letterSpacing: '-0.01em', cursor: 'pointer', ...style }}>{children}</button>;
};

// — A simulated phone/tablet body. No actual bezel; just a content frame at exact pixel width.
const Frame = ({ w, h, t = T, children, statusBar = true, label }) => (
  <div style={{ width: w, height: h, background: t.surface, color: t.text, fontFamily: 'Inter Tight', display: 'flex', flexDirection: 'column', overflow: 'hidden', position: 'relative', border: `1px solid ${t.hair}` }}>
    {statusBar && (
      <div style={{ height: 28, padding: '0 16px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', fontFamily: 'Inter Tight', fontSize: 12, fontWeight: 600, color: t.text, flexShrink: 0 }}>
        <span>9:41</span>
        <div style={{ display: 'flex', gap: 4, alignItems: 'center', fontFamily: 'JetBrains Mono', fontSize: 9, letterSpacing: '0.08em', color: t.textMuted }}>
          <span>5G</span><span>·</span><span>96%</span>
        </div>
      </div>
    )}
    <div style={{ flex: 1, overflow: 'hidden', position: 'relative' }}>{children}</div>
    {label && <Mono muted size={8.5} t={t} style={{ position: 'absolute', bottom: 6, right: 8, opacity: 0.5 }}>{label}</Mono>}
  </div>
);

// ─── ANNOTATION HELPERS ─────────────────────────────────────────────
const Note = ({ children, w = 360 }) => (
  <div style={{ width: w, fontFamily: 'JetBrains Mono', fontSize: 10.5, lineHeight: 1.6, color: T.textMuted, letterSpacing: '0.02em' }}>
    {children}
  </div>
);
const Tag = ({ children, kind = 'info' }) => {
  const kinds = {
    info: { bg: 'transparent', bd: T.hair, fg: T.textMuted },
    accent: { bg: T.accent, bd: T.accent, fg: T.onAccent },
    text: { bg: T.text, bd: T.text, fg: T.surface },
  }[kind];
  return <span style={{ display: 'inline-block', padding: '3px 7px', fontFamily: 'JetBrains Mono', fontSize: 9.5, letterSpacing: '0.12em', textTransform: 'uppercase', background: kinds.bg, color: kinds.fg, border: `1px solid ${kinds.bd}` }}>{children}</span>;
};

// ───────────────────────────────────────────────────────────────────
// MARKETING HOME — MOBILE 375
// ───────────────────────────────────────────────────────────────────
const NavMobile = ({ t = T, open }) => (
  <header style={{ padding: '14px 18px', borderBottom: `1px solid ${t.hair}`, display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexShrink: 0 }}>
    <Logo size={20} t={t} />
    <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
      <button style={{ background: 'transparent', border: 'none', color: t.text, padding: 0, width: 24, height: 18, display: 'flex', flexDirection: 'column', justifyContent: 'space-between', cursor: 'pointer' }}>
        <span style={{ width: '100%', height: 1.5, background: t.text }} />
        <span style={{ width: '100%', height: 1.5, background: t.text }} />
        <span style={{ width: '100%', height: 1.5, background: t.text }} />
      </button>
    </div>
  </header>
);

const MarketingHome375 = ({ t = T }) => (
  <Frame w={375} h={2280} t={t} statusBar={false} label="HOME · MOBILE 375">
    <NavMobile t={t} />
    <div style={{ flex: 1, overflow: 'auto' }}>
      {/* Hero */}
      <section style={{ padding: '28px 18px 24px' }}>
        <Mono muted t={t} size={9}>HRM · PRIVATE BETA · MAY 2026</Mono>
        <h1 style={{ fontSize: 42, fontWeight: 800, letterSpacing: '-0.04em', lineHeight: 0.98, margin: '12px 0 14px', color: t.text }}>
          Read the bylaw <HL t={t}>like an expert.</HL>
        </h1>
        <p style={{ fontSize: 15, lineHeight: 1.5, color: t.textMuted, margin: '0 0 18px' }}>
          ABS reads the HRM Land Use By-law, applied to your parcel. Ask in plain English. Get a sourced reference in seconds.
        </p>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <Btn variant="accent" size="lg" t={t} style={{ width: '100%' }}>Request access →</Btn>
          <Btn variant="ghost" size="lg" t={t} style={{ width: '100%' }}>See pricing</Btn>
        </div>
        {/* Agent reader card */}
        <div style={{ marginTop: 22, background: t.surfaceAlt, border: `1px solid ${t.hair}`, padding: 0, display: 'flex', flexDirection: 'column' }}>
          <div style={{ padding: '10px 14px', borderBottom: `1px solid ${t.hair}`, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <span style={{ width: 5, height: 5, background: t.accent, borderRadius: '50%' }} />
              <Mono muted t={t} size={8.5}>READING · LIVE</Mono>
            </div>
            <Mono muted t={t} size={8.5}>5184 MORRIS ST · ER-1</Mono>
          </div>
          <div style={{ padding: '14px 14px 16px', display: 'flex', flexDirection: 'column', gap: 10 }}>
            <Mono muted t={t} size={8}>QUERY</Mono>
            <div style={{ fontSize: 13.5, fontStyle: 'italic', color: t.textMuted }}>"Can I add a backyard suite?"</div>
            <Hair t={t} />
            <Mono muted t={t} size={8}>RESPONSE</Mono>
            <div style={{ fontSize: 24, fontWeight: 800, letterSpacing: '-0.03em', lineHeight: 1.05, color: t.text }}>Yes — up to 80 m².</div>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              <Tag>§ 9.4</Tag><Tag>0.94 CONF</Tag><Tag kind="accent">VERIFIED</Tag>
            </div>
          </div>
          <div style={{ padding: '8px 14px', borderTop: `1px solid ${t.hair}`, display: 'flex', justifyContent: 'space-between' }}>
            <div style={{ display: 'flex', gap: 3 }}>{[0, 1, 2].map(i => <span key={i} style={{ width: 14, height: 1, background: i === 0 ? t.accent : t.hair }} />)}</div>
            <Mono muted t={t} size={8}>1 / 3</Mono>
          </div>
        </div>
        {/* Stats stacked */}
        <div style={{ marginTop: 22, display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12, paddingTop: 16, borderTop: `1px solid ${t.hair}` }}>
          {[['HRM', 'JURIS'], ['38,420', 'PARCELS'], ['0.94', 'AVG CONF']].map(([n, l]) => (
            <div key={l} style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <span style={{ fontSize: 17, fontWeight: 700, letterSpacing: '-0.02em' }}>{n}</span>
              <Mono muted t={t} size={8}>{l}</Mono>
            </div>
          ))}
        </div>
      </section>

      {/* How it works — stacked */}
      <section style={{ padding: '32px 18px 12px', borderTop: `1px solid ${t.hair}` }}>
        <Mono muted t={t} size={9}>HOW IT WORKS · 3 STEPS</Mono>
        <h2 style={{ fontSize: 28, fontWeight: 800, letterSpacing: '-0.035em', lineHeight: 1.05, margin: '10px 0 20px' }}>The bylaw, read for you.</h2>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 0, border: `1px solid ${t.hair}` }}>
          {[
            { n: '01', t: 'Ask', d: '"Can I add a backyard suite?" Plain English.' },
            { n: '02', t: 'ABS reads', d: 'The agent opens the right sections, runs the math.' },
            { n: '03', t: 'You get a reference', d: 'Verdict, reasoning, citations — ready to attach.' },
          ].map((s, i) => (
            <div key={s.n} style={{ padding: '20px 18px', borderBottom: i < 2 ? `1px solid ${t.hair}` : 'none', display: 'flex', flexDirection: 'column', gap: 10 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <Mono muted t={t} size={10}>{s.n}</Mono>
                <span style={{ width: 16, height: 1, background: t.accent }} />
              </div>
              <div style={{ fontSize: 22, fontWeight: 700, letterSpacing: '-0.025em', lineHeight: 1.05 }}>{s.t}</div>
              <div style={{ fontSize: 13, lineHeight: 1.5, color: t.textMuted }}>{s.d}</div>
            </div>
          ))}
        </div>
      </section>

      {/* Proof grid — 1 col */}
      <section style={{ padding: '32px 18px 12px', borderTop: `1px solid ${t.hair}` }}>
        <Mono muted t={t} size={9}>REAL READINGS · ANONYMIZED</Mono>
        <h2 style={{ fontSize: 28, fontWeight: 800, letterSpacing: '-0.035em', lineHeight: 1.05, margin: '10px 0 16px' }}>What ABS answered this week.</h2>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {[
            { a: '5184 Morris St · ER-1', q: 'Backyard suite?', v: 'Yes — to 80 m².', c: 'HRM LUB § 9.4', f: true },
            { a: '1208 Robie St · COR', q: 'Max height?', v: '20 m by-right.', c: '§ 6.2.3' },
            { a: '17 Edward St · ER-2', q: 'Subdivide?', v: 'No — 1.4 m short.', c: '§ 4.3' },
            { a: '2310 Gottingen St', q: 'Commercial?', v: 'Permitted, gnd floor.', c: '§ 7.1' },
          ].map((p, i) => (
            <div key={i} style={{ background: p.f ? t.accent : t.surfaceAlt, color: p.f ? t.onAccent : t.text, border: p.f ? 'none' : `1px solid ${t.hair}`, padding: 16, display: 'flex', flexDirection: 'column', gap: 8 }}>
              <Mono t={t} size={8.5} style={{ color: p.f ? 'rgba(10,10,10,0.65)' : t.textMuted }}>{p.a}</Mono>
              <div style={{ fontSize: 12.5, fontStyle: 'italic', color: p.f ? 'rgba(10,10,10,0.78)' : t.textMuted }}>"{p.q}"</div>
              <div style={{ fontSize: 22, fontWeight: 800, letterSpacing: '-0.025em', lineHeight: 1.1 }}>{p.v}</div>
              <Mono t={t} size={8.5} style={{ color: p.f ? 'rgba(10,10,10,0.65)' : t.textMuted }}>{p.c}</Mono>
            </div>
          ))}
        </div>
      </section>

      {/* Closing CTA */}
      <section style={{ padding: '28px 18px 36px', borderTop: `1px solid ${t.hair}` }}>
        <div style={{ background: t.surfaceInk, color: t.surface, padding: '28px 22px', display: 'flex', flexDirection: 'column', gap: 16 }}>
          <h2 style={{ fontSize: 30, fontWeight: 800, letterSpacing: '-0.035em', lineHeight: 1, margin: 0 }}>An expert planner, <span style={{ color: t.accent }}>in your pocket.</span></h2>
          <p style={{ fontSize: 13.5, lineHeight: 1.5, color: 'rgba(237,232,219,0.7)', margin: 0 }}>Invite-only beta. Tell us about your project.</p>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <Btn variant="accent" size="lg" t={t} style={{ width: '100%' }}>Request access →</Btn>
            <Btn variant="ghost" size="lg" t={t} style={{ width: '100%', borderColor: t.surface, color: t.surface }}>See pricing</Btn>
          </div>
        </div>
      </section>

      {/* Footer mini */}
      <footer style={{ padding: '20px 18px', borderTop: `1px solid ${t.hair}`, display: 'flex', justifyContent: 'space-between' }}>
        <Mono muted t={t} size={8}>© 2026 ABS · HALIFAX</Mono>
        <Mono muted t={t} size={8}>NOT LEGAL ADVICE</Mono>
      </footer>
    </div>
  </Frame>
);

// ───────────────────────────────────────────────────────────────────
// MARKETING HOME — TABLET 768
// ───────────────────────────────────────────────────────────────────
const NavTablet = ({ t = T }) => (
  <header style={{ padding: '16px 28px', borderBottom: `1px solid ${t.hair}`, display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexShrink: 0 }}>
    <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
      <Logo size={22} t={t} />
      <Hair vertical t={t} style={{ height: 14 }} />
      <Mono muted t={t} size={9.5}>HRM · PRIVATE BETA</Mono>
    </div>
    <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
      {['Home', 'Pricing', 'App'].map((n, i) => (
        <button key={n} style={{ background: 'transparent', border: 'none', color: i === 0 ? t.text : t.textMuted, fontFamily: 'inherit', fontSize: 13, fontWeight: i === 0 ? 600 : 400, padding: '8px 12px', cursor: 'pointer', position: 'relative' }}>
          {n}{i === 0 && <span style={{ position: 'absolute', left: 12, right: 12, bottom: 4, height: 2, background: t.accent }} />}
        </button>
      ))}
      <Btn variant="accent" size="sm" t={t} style={{ marginLeft: 8 }}>Request access</Btn>
    </div>
  </header>
);

const MarketingHome768 = ({ t = T }) => (
  <Frame w={768} h={2120} t={t} statusBar={false} label="HOME · TABLET 768">
    <NavTablet t={t} />
    <div style={{ flex: 1, overflow: 'auto' }}>
      {/* Hero — single column at this width, tighter than desktop */}
      <section style={{ padding: '44px 36px 36px' }}>
        <Mono muted t={t} size={10}>HRM · PRIVATE BETA · MAY 2026</Mono>
        <h1 style={{ fontSize: 64, fontWeight: 800, letterSpacing: '-0.045em', lineHeight: 0.96, margin: '14px 0 18px', maxWidth: 620 }}>
          Read the bylaw <HL t={t}>like an expert.</HL>
        </h1>
        <p style={{ fontSize: 17, lineHeight: 1.5, color: t.textMuted, margin: '0 0 22px', maxWidth: 560 }}>
          ABS reads the HRM Land Use By-law, applied to your specific parcel. Ask in plain English. Get a sourced reference in seconds.
        </p>
        <div style={{ display: 'flex', gap: 10 }}>
          <Btn variant="accent" size="lg" t={t}>Request access →</Btn>
          <Btn variant="ghost" size="lg" t={t}>See pricing</Btn>
        </div>
        {/* Stats inline */}
        <div style={{ marginTop: 28, display: 'flex', gap: 32, paddingTop: 18, borderTop: `1px solid ${t.hair}` }}>
          {[['HRM', 'JURISDICTION'], ['38,420', 'PARCELS INDEXED'], ['0.94', 'AVG. CONFIDENCE']].map(([n, l]) => (
            <div key={l} style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <span style={{ fontSize: 22, fontWeight: 700, letterSpacing: '-0.02em' }}>{n}</span>
              <Mono muted t={t} size={9}>{l}</Mono>
            </div>
          ))}
        </div>
        {/* Agent reader card full width */}
        <div style={{ marginTop: 28, background: t.surfaceAlt, border: `1px solid ${t.hair}`, padding: 0, display: 'flex', flexDirection: 'column' }}>
          <div style={{ padding: '12px 16px', borderBottom: `1px solid ${t.hair}`, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ width: 6, height: 6, background: t.accent, borderRadius: '50%' }} />
              <Mono muted t={t} size={9.5}>READING · LIVE</Mono>
            </div>
            <Mono muted t={t} size={9.5}>5184 MORRIS ST · ER-1</Mono>
          </div>
          <div style={{ padding: '20px 24px', display: 'grid', gridTemplateColumns: '1fr 1px 1fr', gap: 20 }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              <Mono muted t={t} size={8.5}>QUERY</Mono>
              <div style={{ fontSize: 15, fontStyle: 'italic', color: t.textMuted, lineHeight: 1.45 }}>"Can my client add a backyard suite to this lot?"</div>
            </div>
            <Hair vertical t={t} />
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              <Mono muted t={t} size={8.5}>RESPONSE</Mono>
              <div style={{ fontSize: 32, fontWeight: 800, letterSpacing: '-0.03em', lineHeight: 1.05 }}>Yes — up to 80 m².</div>
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                <Tag>§ 9.4</Tag><Tag>0.94 CONF</Tag><Tag kind="accent">VERIFIED 2026·05·06</Tag>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* How it works — 3 col stays */}
      <section style={{ padding: '40px 36px', borderTop: `1px solid ${t.hair}` }}>
        <Mono muted t={t} size={10}>HOW IT WORKS · 3 STEPS</Mono>
        <h2 style={{ fontSize: 36, fontWeight: 800, letterSpacing: '-0.035em', lineHeight: 1.05, margin: '12px 0 24px' }}>The bylaw, read for you.</h2>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', border: `1px solid ${t.hair}` }}>
          {[
            { n: '01', t: 'Ask', d: 'Plain English. Type it like you would to a planner.' },
            { n: '02', t: 'ABS reads', d: 'Locates your parcel, opens the relevant sections.' },
            { n: '03', t: 'Reference', d: 'Verdict, reasoning, citations — ready to attach.' },
          ].map((s, i) => (
            <div key={s.n} style={{ padding: '24px 20px', borderRight: i < 2 ? `1px solid ${t.hair}` : 'none', display: 'flex', flexDirection: 'column', gap: 12 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <Mono muted t={t} size={10}>{s.n}</Mono>
                <span style={{ width: 16, height: 1, background: t.accent }} />
              </div>
              <div style={{ fontSize: 22, fontWeight: 700, letterSpacing: '-0.025em' }}>{s.t}</div>
              <div style={{ fontSize: 13, lineHeight: 1.5, color: t.textMuted }}>{s.d}</div>
            </div>
          ))}
        </div>
      </section>

      {/* Proof grid — 2 col */}
      <section style={{ padding: '40px 36px', borderTop: `1px solid ${t.hair}` }}>
        <Mono muted t={t} size={10}>REAL READINGS · ANONYMIZED</Mono>
        <h2 style={{ fontSize: 36, fontWeight: 800, letterSpacing: '-0.035em', lineHeight: 1.05, margin: '12px 0 24px' }}>What ABS answered this week.</h2>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
          {[
            { a: '5184 Morris St · ER-1', q: 'Backyard suite?', v: 'Yes — to 80 m².', c: 'HRM LUB § 9.4', f: true },
            { a: '1208 Robie St · COR', q: 'Max height?', v: '20 m by-right.', c: '§ 6.2.3' },
            { a: '17 Edward St · ER-2', q: 'Subdivide?', v: 'No — 1.4 m short.', c: '§ 4.3' },
            { a: '2310 Gottingen St · DH-1', q: 'Commercial?', v: 'Permitted, gnd floor.', c: '§ 7.1' },
            { a: '46 Crichton Ave · DR', q: 'Side yard?', v: '1.2 m minimum.', c: '§ 5.4' },
            { a: '101 Quinpool Rd · COR', q: 'Parking?', v: 'None — transit.', c: '§ 8.2' },
          ].map((p, i) => (
            <div key={i} style={{ background: p.f ? t.accent : t.surfaceAlt, color: p.f ? t.onAccent : t.text, border: p.f ? 'none' : `1px solid ${t.hair}`, padding: 20, display: 'flex', flexDirection: 'column', gap: 10, minHeight: 180, justifyContent: 'space-between' }}>
              <Mono t={t} size={9} style={{ color: p.f ? 'rgba(10,10,10,0.65)' : t.textMuted }}>{p.a}</Mono>
              <div>
                <div style={{ fontSize: 13, fontStyle: 'italic', color: p.f ? 'rgba(10,10,10,0.78)' : t.textMuted, marginBottom: 6 }}>"{p.q}"</div>
                <div style={{ fontSize: 24, fontWeight: 800, letterSpacing: '-0.025em', lineHeight: 1.1 }}>{p.v}</div>
              </div>
              <Mono t={t} size={9} style={{ color: p.f ? 'rgba(10,10,10,0.65)' : t.textMuted }}>{p.c}</Mono>
            </div>
          ))}
        </div>
      </section>

      {/* Closing CTA */}
      <section style={{ padding: '40px 36px 48px', borderTop: `1px solid ${t.hair}` }}>
        <div style={{ background: t.surfaceInk, color: t.surface, padding: '40px 32px', display: 'grid', gridTemplateColumns: '1.4fr 1fr', gap: 24, alignItems: 'center' }}>
          <h2 style={{ fontSize: 42, fontWeight: 800, letterSpacing: '-0.035em', lineHeight: 1, margin: 0 }}>An expert planner,<br/><span style={{ color: t.accent }}>in your workflow.</span></h2>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <Btn variant="accent" size="lg" t={t}>Request access →</Btn>
            <Btn variant="ghost" size="lg" t={t} style={{ borderColor: t.surface, color: t.surface }}>See pricing</Btn>
          </div>
        </div>
      </section>
    </div>
  </Frame>
);

// ───────────────────────────────────────────────────────────────────
// APP SHELL — MOBILE 375
// Three states: chat default (sidebar closed), drawer open, parcel sheet
// ───────────────────────────────────────────────────────────────────

const AppHeaderMobile = ({ t = T, onSidebar, onParcel }) => (
  <div style={{ padding: '10px 14px', borderBottom: `1px solid ${t.hair}`, display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexShrink: 0 }}>
    <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
      <button style={{ background: 'transparent', border: 'none', padding: 0, width: 22, height: 16, display: 'flex', flexDirection: 'column', justifyContent: 'space-between', cursor: 'pointer' }}>
        <span style={{ width: '100%', height: 1.5, background: t.text }} />
        <span style={{ width: '100%', height: 1.5, background: t.text }} />
        <span style={{ width: '100%', height: 1.5, background: t.text }} />
      </button>
      <Logo size={18} t={t} />
    </div>
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <Mono muted t={t} size={8}>ER-1</Mono>
      <span style={{ width: 5, height: 5, background: t.accent, borderRadius: '50%' }} />
    </div>
  </div>
);

const AddrPill = ({ t = T }) => (
  <button style={{ width: '100%', padding: '10px 14px', borderBottom: `1px solid ${t.hair}`, background: t.surfaceAlt, border: 'none', borderBottomStyle: 'solid', borderBottomWidth: 1, borderBottomColor: t.hair, display: 'flex', justifyContent: 'space-between', alignItems: 'center', cursor: 'pointer', textAlign: 'left' }}>
    <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
      <span style={{ fontSize: 13.5, fontWeight: 600, color: t.text }}>5184 Morris St</span>
      <Mono muted t={t} size={8}>372 m² · ER-1 · TAP FOR PARCEL</Mono>
    </div>
    <span style={{ color: t.textMuted, fontSize: 14 }}>▴</span>
  </button>
);

const ChatThread = ({ t = T }) => (
  <div style={{ flex: 1, overflowY: 'auto', padding: '16px 14px 14px', display: 'flex', flexDirection: 'column', gap: 16, background: t.surface }}>
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontFamily: 'JetBrains Mono', fontSize: 9.5, color: t.textMuted, letterSpacing: '0.04em' }}>
      <Hair t={t} style={{ flex: 1 }} /><span>READING · ER-1</span><Hair t={t} style={{ flex: 1 }} />
    </div>
    <div style={{ alignSelf: 'flex-end', maxWidth: '82%', background: t.text, color: t.surface, padding: '10px 13px', fontSize: 13.5, lineHeight: 1.45 }}>
      Can my client add a backyard suite? Around 75 m².
    </div>
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <Logo size={14} t={t} />
        <Mono muted t={t} size={8.5}>ABS° · AGENT</Mono>
        <span style={{ flex: 1 }} />
        <Mono accent t={t} size={8.5}>94% CONF</Mono>
      </div>
      <div style={{ fontSize: 20, fontWeight: 800, letterSpacing: '-0.03em', lineHeight: 1.1 }}>Yes — up to 80 m².</div>
      <div style={{ fontSize: 13, lineHeight: 1.5, color: t.textMuted }}>Permitted as a secondary use in ER-1. 75 m² target clears the 80 m² cap. Verify rear yard depth and separation from main dwelling.</div>
      <button style={{ background: 'transparent', border: `1px solid ${t.hair}`, color: t.textMuted, padding: '6px 10px', fontFamily: 'JetBrains Mono', fontSize: 9.5, letterSpacing: '0.08em', alignSelf: 'flex-start', display: 'inline-flex', gap: 6, cursor: 'pointer' }}>
        <span>▸</span><span>4 REASONING STEPS</span>
      </button>
      <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap' }}>
        <Tag>§ 9.4</Tag><Tag>§ 5.4</Tag>
      </div>
    </div>
    <div style={{ alignSelf: 'flex-end', maxWidth: '82%', background: t.text, color: t.surface, padding: '10px 13px', fontSize: 13.5, lineHeight: 1.45 }}>
      What about height?
    </div>
  </div>
);

const Composer = ({ t = T, focused }) => (
  <div style={{ borderTop: `1px solid ${t.hair}`, padding: '10px 14px 12px', background: t.surface, flexShrink: 0 }}>
    {!focused && (
      <div style={{ display: 'flex', gap: 6, marginBottom: 8, overflowX: 'auto' }}>
        {['Yard?', 'Height?', 'Permits?'].map(s => (
          <button key={s} style={{ background: t.surfaceAlt, border: `1px solid ${t.hair}`, color: t.text, padding: '5px 10px', cursor: 'pointer', fontFamily: 'inherit', fontSize: 11.5, whiteSpace: 'nowrap' }}>{s}</button>
        ))}
      </div>
    )}
    <div style={{ display: 'flex', border: `1.5px solid ${focused ? t.accent : t.text}`, background: t.surface }}>
      <textarea placeholder="Ask about this parcel…" rows={1} style={{ flex: 1, padding: '10px 12px', border: 'none', resize: 'none', background: 'transparent', color: t.text, fontFamily: 'inherit', fontSize: 14, outline: 'none', minHeight: 38 }} />
      <button style={{ background: t.accent, color: t.onAccent, border: 'none', padding: '0 14px', fontFamily: 'inherit', fontWeight: 600, fontSize: 13, cursor: 'pointer' }}>→</button>
    </div>
  </div>
);

// State A: default chat, sidebar closed
const AppMobileChat = ({ t = T }) => (
  <Frame w={375} h={812} t={t} label="APP · CHAT · SIDEBAR CLOSED">
    <AppHeaderMobile t={t} />
    <AddrPill t={t} />
    <ChatThread t={t} />
    <Composer t={t} />
  </Frame>
);

// State B: drawer open
const AppMobileDrawer = ({ t = T }) => (
  <Frame w={375} h={812} t={t} label="APP · DRAWER OPEN">
    {/* dimmed underlay */}
    <div style={{ position: 'absolute', inset: 0, background: 'rgba(0,0,0,0.5)', zIndex: 1 }} />
    <div style={{ position: 'absolute', inset: 0, opacity: 0.35 }}>
      <AppHeaderMobile t={t} />
      <AddrPill t={t} />
      <ChatThread t={t} />
    </div>
    {/* drawer */}
    <aside style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: 300, background: t.surface, borderRight: `1px solid ${t.hair}`, zIndex: 2, display: 'flex', flexDirection: 'column', boxShadow: '0 0 40px rgba(0,0,0,0.5)' }}>
      <div style={{ padding: '12px 14px', borderBottom: `1px solid ${t.hair}`, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <Logo size={18} t={t} />
        <button style={{ background: 'transparent', border: 'none', color: t.textMuted, fontSize: 16, cursor: 'pointer' }}>✕</button>
      </div>
      <div style={{ padding: '10px 14px', borderBottom: `1px solid ${t.hair}` }}>
        <Btn variant="primary" size="sm" t={t} style={{ width: '100%' }}>+ New reading</Btn>
      </div>
      <div style={{ padding: '10px 14px 4px' }}>
        <Mono muted t={t} size={8.5}>RECENT · 6</Mono>
      </div>
      <div style={{ flex: 1, overflowY: 'auto', padding: '0 6px 12px' }}>
        {[
          { a: '5184 Morris St', t: 'Backyard suite feas.', z: 'ER-1', u: '2M', active: true },
          { a: '1208 Robie St', t: 'Max height + bonusing', z: 'COR', u: '1H' },
          { a: '17 Edward St', t: 'Subdivision check', z: 'ER-2', u: 'YDAY', unread: true },
          { a: '2310 Gottingen St', t: 'Ground-floor comm.', z: 'DH-1', u: '2D' },
          { a: '46 Crichton Ave', t: 'Side yard minimums', z: 'DR', u: '3D' },
        ].map(th => (
          <div key={th.a} style={{ background: th.active ? t.surfaceAlt : 'transparent', borderLeft: th.active ? `2px solid ${t.accent}` : '2px solid transparent', padding: '9px 11px', display: 'flex', flexDirection: 'column', gap: 3, marginBottom: 1 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
              <span style={{ fontSize: 12, fontWeight: 600 }}>{th.a}</span>
              {th.unread && <span style={{ width: 5, height: 5, background: t.accent, borderRadius: '50%' }} />}
            </div>
            <span style={{ fontSize: 11, color: t.textMuted }}>{th.t}</span>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 1 }}>
              <Mono muted t={t} size={8}>{th.z}</Mono><Mono muted t={t} size={8}>{th.u}</Mono>
            </div>
          </div>
        ))}
      </div>
      <div style={{ padding: '10px 14px', borderTop: `1px solid ${t.hair}`, display: 'flex', alignItems: 'center', gap: 9 }}>
        <div style={{ width: 24, height: 24, background: t.accent, color: t.onAccent, display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: 'JetBrains Mono', fontSize: 9.5, fontWeight: 600 }}>HS</div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 11.5, fontWeight: 600 }}>Halifax Studio</div>
          <Mono muted t={t} size={8}>PRACTICE · 4 SEATS</Mono>
        </div>
      </div>
    </aside>
  </Frame>
);

// State C: parcel bottom sheet open
const AppMobileSheet = ({ t = T }) => (
  <Frame w={375} h={812} t={t} label="APP · PARCEL SHEET">
    <div style={{ position: 'absolute', inset: 0, opacity: 0.35 }}>
      <AppHeaderMobile t={t} />
      <AddrPill t={t} />
      <ChatThread t={t} />
      <Composer t={t} />
    </div>
    <div style={{ position: 'absolute', inset: 0, background: 'rgba(0,0,0,0.5)', zIndex: 1 }} />
    <aside style={{ position: 'absolute', left: 0, right: 0, bottom: 0, maxHeight: '78%', background: t.surfaceAlt, borderTop: `1px solid ${t.hair}`, zIndex: 2, display: 'flex', flexDirection: 'column', boxShadow: '0 -20px 40px rgba(0,0,0,0.5)' }}>
      <div style={{ padding: '8px 0 0', display: 'flex', justifyContent: 'center' }}>
        <div style={{ width: 36, height: 4, background: t.hair, borderRadius: 2 }} />
      </div>
      <div style={{ padding: '14px 18px 8px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <Mono muted t={t} size={9}>PARCEL</Mono>
          <div style={{ fontSize: 20, fontWeight: 700, letterSpacing: '-0.02em', marginTop: 4 }}>5184 Morris St</div>
          <Mono muted t={t} size={9}>HALIFAX, NS · ER-1</Mono>
        </div>
        <button style={{ background: 'transparent', border: 'none', color: t.textMuted, fontSize: 18, cursor: 'pointer' }}>✕</button>
      </div>
      <div style={{ flex: 1, overflowY: 'auto', padding: '4px 18px 18px', display: 'flex', flexDirection: 'column', gap: 14 }}>
        {/* site plan */}
        <div style={{ aspectRatio: '4 / 3', background: t.surface, border: `1px solid ${t.hair}`, position: 'relative' }}>
          <svg viewBox="0 0 200 150" style={{ width: '100%', height: '100%' }}>
            <rect x="20" y="20" width="160" height="110" fill="none" stroke={t.text} strokeWidth="0.6" />
            <rect x="36" y="36" width="128" height="78" fill="none" stroke={t.text} strokeWidth="0.4" strokeDasharray="2 2" />
            <rect x="56" y="48" width="60" height="40" fill={t.text} fillOpacity="0.15" stroke={t.text} strokeWidth="0.6" />
            <rect x="120" y="68" width="34" height="34" fill={t.accent} stroke={t.accent} strokeWidth="0.6" />
            <text x="137" y="88" fontSize="5" fill={t.onAccent} fontFamily="JetBrains Mono" textAnchor="middle">SUITE</text>
          </svg>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {[['Lot area', '372 m²'], ['Frontage', '11.4 m'], ['Zoning', 'ER-1'], ['Existing units', '1 (1924)'], ['Heritage', 'No'], ['Transit zone', 'Yes']].map(([k, v]) => (
            <div key={k} style={{ display: 'flex', justifyContent: 'space-between', borderBottom: `1px dotted ${t.hair}`, paddingBottom: 5, fontSize: 12 }}>
              <Mono muted t={t} size={9}>{k}</Mono>
              <span style={{ fontWeight: 600 }}>{v}</span>
            </div>
          ))}
        </div>
        <Mono muted t={t} size={9}>CITED THIS THREAD · 3</Mono>
        {[['§ 9.4', 'Backyard Suites'], ['§ 5.4', 'Yard Requirements'], ['§ 2.8', 'Variances']].map(([c, n]) => (
          <div key={c} style={{ background: t.surface, border: `1px solid ${t.hair}`, padding: '8px 11px', display: 'flex', justifyContent: 'space-between' }}>
            <Mono accent t={t} size={10}>{c}</Mono>
            <span style={{ fontSize: 12 }}>{n}</span>
          </div>
        ))}
      </div>
      <div style={{ padding: 14, borderTop: `1px solid ${t.hair}`, display: 'flex', gap: 8 }}>
        <Btn variant="primary" size="sm" t={t} style={{ flex: 1 }}>Export PDF</Btn>
        <Btn variant="ghost" size="sm" t={t} style={{ flex: 1 }}>Share</Btn>
      </div>
    </aside>
  </Frame>
);

// ───────────────────────────────────────────────────────────────────
// APP SHELL — TABLET 768
// ───────────────────────────────────────────────────────────────────

const SidebarTablet = ({ t = T, collapsed }) => (
  <aside style={{ width: collapsed ? 56 : 240, borderRight: `1px solid ${t.hair}`, background: t.surface, display: 'flex', flexDirection: 'column', flexShrink: 0 }}>
    <div style={{ padding: collapsed ? '12px 0' : '12px 14px', borderBottom: `1px solid ${t.hair}`, display: 'flex', justifyContent: 'center' }}>
      {collapsed ? (
        <Logo size={20} t={t} />
      ) : (
        <Btn variant="primary" size="sm" t={t} style={{ width: '100%' }}>+ New reading</Btn>
      )}
    </div>
    {!collapsed && <div style={{ padding: '10px 14px 4px' }}><Mono muted t={t} size={8.5}>RECENT · 6</Mono></div>}
    <div style={{ flex: 1, overflowY: 'auto', padding: collapsed ? '8px 0' : '0 6px 12px' }}>
      {[
        { a: '5184 Morris St', t: 'Backyard suite', z: 'ER-1', u: '2M', active: true, i: 'M' },
        { a: '1208 Robie St', t: 'Max height', z: 'COR', u: '1H', i: 'R' },
        { a: '17 Edward St', t: 'Subdivision', z: 'ER-2', u: 'YDAY', unread: true, i: 'E' },
        { a: '2310 Gottingen St', t: 'Ground-floor', z: 'DH-1', u: '2D', i: 'G' },
        { a: '46 Crichton Ave', t: 'Side yard', z: 'DR', u: '3D', i: 'C' },
      ].map(th => collapsed ? (
        <div key={th.a} style={{ padding: '6px 0', display: 'flex', justifyContent: 'center', position: 'relative' }}>
          {th.active && <span style={{ position: 'absolute', left: 0, top: 6, bottom: 6, width: 2, background: t.accent }} />}
          <div style={{ width: 28, height: 28, background: th.active ? t.surfaceAlt : 'transparent', border: `1px solid ${t.hair}`, display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: 'JetBrains Mono', fontSize: 11, color: t.text, position: 'relative' }}>
            {th.i}
            {th.unread && <span style={{ position: 'absolute', top: -2, right: -2, width: 6, height: 6, background: t.accent, borderRadius: '50%' }} />}
          </div>
        </div>
      ) : (
        <div key={th.a} style={{ background: th.active ? t.surfaceAlt : 'transparent', borderLeft: th.active ? `2px solid ${t.accent}` : '2px solid transparent', padding: '9px 11px', display: 'flex', flexDirection: 'column', gap: 3, marginBottom: 1 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
            <span style={{ fontSize: 12, fontWeight: 600 }}>{th.a}</span>
            {th.unread && <span style={{ width: 5, height: 5, background: t.accent, borderRadius: '50%' }} />}
          </div>
          <span style={{ fontSize: 11, color: t.textMuted }}>{th.t}</span>
          <div style={{ display: 'flex', justifyContent: 'space-between' }}>
            <Mono muted t={t} size={8}>{th.z}</Mono><Mono muted t={t} size={8}>{th.u}</Mono>
          </div>
        </div>
      ))}
    </div>
    <div style={{ padding: collapsed ? '10px 0' : '10px 14px', borderTop: `1px solid ${t.hair}`, display: 'flex', justifyContent: 'center', alignItems: 'center', gap: 9 }}>
      <div style={{ width: 24, height: 24, background: t.accent, color: t.onAccent, display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: 'JetBrains Mono', fontSize: 9.5, fontWeight: 600 }}>HS</div>
      {!collapsed && (<div style={{ flex: 1, minWidth: 0 }}><div style={{ fontSize: 11.5, fontWeight: 600 }}>Halifax Studio</div><Mono muted t={t} size={8}>PRACTICE</Mono></div>)}
    </div>
  </aside>
);

const AppMainTablet = ({ t = T, showFAB }) => (
  <main style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0, background: t.surface, position: 'relative' }}>
    <div style={{ padding: '10px 18px', borderBottom: `1px solid ${t.hair}`, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
      <Mono muted t={t} size={9.5}>READING · 5184 MORRIS ST · ER-1</Mono>
      <Mono muted t={t} size={9.5}>VERIFIED 2026·05·06</Mono>
    </div>
    <div style={{ flex: 1, overflowY: 'auto', padding: '20px 24px', display: 'flex', flexDirection: 'column', gap: 18 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontFamily: 'JetBrains Mono', fontSize: 10, color: t.textMuted, letterSpacing: '0.04em' }}>
        <Hair t={t} style={{ flex: 1 }} /><span>READING · ER-1</span><Hair t={t} style={{ flex: 1 }} />
      </div>
      <div style={{ alignSelf: 'flex-end', maxWidth: '72%', background: t.text, color: t.surface, padding: '11px 15px', fontSize: 13.5, lineHeight: 1.5 }}>
        Can my client add a backyard suite? Around 75 m².
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 11 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
          <Logo size={16} t={t} /><Mono muted t={t} size={9.5}>ABS° · AGENT</Mono>
          <span style={{ flex: 1 }} /><Mono accent t={t} size={9.5}>94% CONF</Mono>
        </div>
        <div style={{ fontSize: 24, fontWeight: 800, letterSpacing: '-0.03em', lineHeight: 1.1 }}>Yes — up to 80 m².</div>
        <div style={{ fontSize: 14, lineHeight: 1.55, color: t.textMuted, maxWidth: 540 }}>Permitted as a secondary use in ER-1. 75 m² target clears the 80 m² cap. Verify rear yard depth and separation.</div>
        <button style={{ background: 'transparent', border: `1px solid ${t.hair}`, color: t.textMuted, padding: '6px 10px', fontFamily: 'JetBrains Mono', fontSize: 10, letterSpacing: '0.08em', alignSelf: 'flex-start', cursor: 'pointer' }}>▸ 4 REASONING STEPS</button>
        <div style={{ display: 'flex', gap: 6 }}><Tag>§ 9.4</Tag><Tag>§ 5.4</Tag></div>
      </div>
    </div>
    <div style={{ borderTop: `1px solid ${t.hair}`, padding: '12px 24px 14px', background: t.surface }}>
      <div style={{ display: 'flex', gap: 6, marginBottom: 9 }}>
        {['Yard?', 'Height?', 'Permits?', 'Compare RT-2'].map(s => (
          <button key={s} style={{ background: t.surfaceAlt, border: `1px solid ${t.hair}`, color: t.text, padding: '5px 9px', fontFamily: 'inherit', fontSize: 11.5, cursor: 'pointer' }}>{s}</button>
        ))}
      </div>
      <div style={{ display: 'flex', border: `1.5px solid ${t.text}` }}>
        <textarea placeholder="Ask about this parcel — yard, height, use, density…" rows={1} style={{ flex: 1, padding: '11px 14px', border: 'none', resize: 'none', background: 'transparent', color: t.text, fontFamily: 'inherit', fontSize: 14, outline: 'none', minHeight: 40 }} />
        <button style={{ background: t.accent, color: t.onAccent, border: 'none', padding: '0 18px', fontWeight: 600, fontSize: 13, cursor: 'pointer' }}>Send →</button>
      </div>
    </div>
    {showFAB && (
      <button style={{ position: 'absolute', right: 18, bottom: 96, background: t.text, color: t.surface, border: `1.5px solid ${t.text}`, padding: '10px 14px', fontFamily: 'inherit', fontWeight: 600, fontSize: 12, letterSpacing: '-0.005em', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 8 }}>
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none"><rect x="1" y="1" width="12" height="9" stroke="currentColor" strokeWidth="1.2" /><rect x="3.5" y="3.5" width="7" height="4" stroke="currentColor" strokeWidth="0.8" /></svg>
        <span>Parcel</span>
      </button>
    )}
  </main>
);

const ParcelPaneTablet = ({ t = T }) => (
  <aside style={{ width: 280, borderLeft: `1px solid ${t.hair}`, background: t.surfaceAlt, padding: 16, display: 'flex', flexDirection: 'column', gap: 12, overflowY: 'auto', flexShrink: 0 }}>
    <div style={{ display: 'flex', justifyContent: 'space-between' }}><Mono muted t={t} size={9}>PARCEL</Mono><button style={{ background: 'transparent', border: `1px solid ${t.hair}`, color: t.text, padding: '3px 7px', fontFamily: 'JetBrains Mono', fontSize: 9, letterSpacing: '0.1em', cursor: 'pointer' }}>CHANGE</button></div>
    <div><div style={{ fontSize: 18, fontWeight: 700, letterSpacing: '-0.02em' }}>5184 Morris St</div><Mono muted t={t} size={9}>HALIFAX · ER-1</Mono></div>
    <div style={{ aspectRatio: '4 / 3', background: t.surface, border: `1px solid ${t.hair}` }}>
      <svg viewBox="0 0 200 150" style={{ width: '100%', height: '100%' }}>
        <rect x="20" y="20" width="160" height="110" fill="none" stroke={t.text} strokeWidth="0.6" />
        <rect x="36" y="36" width="128" height="78" fill="none" stroke={t.text} strokeWidth="0.4" strokeDasharray="2 2" />
        <rect x="56" y="48" width="60" height="40" fill={t.text} fillOpacity="0.15" stroke={t.text} strokeWidth="0.6" />
        <rect x="120" y="68" width="34" height="34" fill={t.accent} stroke={t.accent} strokeWidth="0.6" />
      </svg>
    </div>
    <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
      {[['Lot', '372 m²'], ['Frontage', '11.4 m'], ['Zoning', 'ER-1'], ['Units', '1 (1924)']].map(([k, v]) => (
        <div key={k} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11.5, borderBottom: `1px dotted ${t.hair}`, paddingBottom: 4 }}>
          <Mono muted t={t} size={8.5}>{k}</Mono><span style={{ fontWeight: 600 }}>{v}</span>
        </div>
      ))}
    </div>
    <Mono muted t={t} size={9}>CITED · 3</Mono>
    {[['§ 9.4', 'Backyard Suites'], ['§ 5.4', 'Yards']].map(([c, n]) => (
      <div key={c} style={{ background: t.surface, border: `1px solid ${t.hair}`, padding: '7px 10px', display: 'flex', justifyContent: 'space-between' }}>
        <Mono accent t={t} size={9.5}>{c}</Mono><span style={{ fontSize: 11.5 }}>{n}</span>
      </div>
    ))}
    <div style={{ marginTop: 'auto', paddingTop: 10, borderTop: `1px solid ${t.hair}`, display: 'flex', flexDirection: 'column', gap: 6 }}>
      <Btn variant="primary" size="sm" t={t} style={{ width: '100%' }}>Export PDF</Btn>
    </div>
  </aside>
);

const AppTabletCollapsed = ({ t = T }) => (
  <Frame w={768} h={1024} t={t} label="APP · TABLET · SIDEBAR ICONS, PARCEL CLOSED">
    <div style={{ flex: 1, display: 'flex', minHeight: 0 }}>
      <SidebarTablet t={t} collapsed />
      <AppMainTablet t={t} showFAB />
    </div>
  </Frame>
);

const AppTabletExpanded = ({ t = T }) => (
  <Frame w={768} h={1024} t={t} label="APP · TABLET · SIDEBAR OPEN, PARCEL CLOSED">
    <div style={{ flex: 1, display: 'flex', minHeight: 0 }}>
      <SidebarTablet t={t} />
      <AppMainTablet t={t} showFAB />
    </div>
  </Frame>
);

const AppTabletParcel = ({ t = T }) => (
  <Frame w={768} h={1024} t={t} label="APP · TABLET · SIDEBAR ICONS, PARCEL OPEN">
    <div style={{ flex: 1, display: 'flex', minHeight: 0 }}>
      <SidebarTablet t={t} collapsed />
      <AppMainTablet t={t} />
      <ParcelPaneTablet t={t} />
    </div>
  </Frame>
);

// ───────────────────────────────────────────────────────────────────
// AUTH — sanity check
// ───────────────────────────────────────────────────────────────────

const FormField = ({ t = T, label, type = 'text', placeholder, hint }) => (
  <label style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
    <Mono muted t={t} size={9}>{label}</Mono>
    <input type={type} placeholder={placeholder} style={{ padding: '11px 13px', background: t.surface, color: t.text, border: `1.5px solid ${t.text}`, fontFamily: 'inherit', fontSize: 14, outline: 'none' }} />
    {hint && <span style={{ fontSize: 11.5, color: t.textMuted }}>{hint}</span>}
  </label>
);

const AuthMobileLogin = ({ t = T }) => (
  <Frame w={375} h={812} t={t} label="LOG IN · MOBILE 375">
    <NavMobile t={t} />
    <div style={{ flex: 1, overflow: 'auto', padding: '32px 18px' }}>
      <Mono muted t={t} size={9.5}>LOG IN · ABS°</Mono>
      <h1 style={{ fontSize: 38, fontWeight: 800, letterSpacing: '-0.04em', lineHeight: 1, margin: '12px 0 10px' }}>Welcome <HL t={t}>back.</HL></h1>
      <p style={{ fontSize: 14, color: t.textMuted, lineHeight: 1.5, margin: '0 0 22px' }}>Pick up where you left your last reading.</p>
      <form style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
        <FormField t={t} label="EMAIL" type="email" placeholder="you@firm.com" />
        <FormField t={t} label="PASSWORD" type="password" placeholder="••••••••" />
        <div style={{ display: 'flex', justifyContent: 'flex-end' }}><span style={{ fontSize: 11.5, color: t.textMuted }}>Forgot password?</span></div>
        <Btn variant="accent" size="lg" t={t} style={{ width: '100%' }}>Log in →</Btn>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, margin: '4px 0' }}><Hair t={t} style={{ flex: 1 }} /><Mono muted t={t} size={8.5}>OR</Mono><Hair t={t} style={{ flex: 1 }} /></div>
        <Btn variant="ghost" size="md" t={t}>Continue with Google</Btn>
        <Btn variant="ghost" size="md" t={t}>Magic link</Btn>
      </form>
      <div style={{ marginTop: 22, padding: 16, background: t.surfaceAlt, border: `1px solid ${t.hair}`, display: 'flex', flexDirection: 'column', gap: 8 }}>
        <Mono muted t={t} size={8.5}>FROM YOUR LAST SESSION</Mono>
        <Mono muted t={t} size={8.5}>5184 MORRIS ST · ER-1</Mono>
        <div style={{ fontSize: 18, fontWeight: 700, letterSpacing: '-0.025em' }}>Yes — up to 80 m².</div>
        <Mono muted t={t} size={8.5}>HRM LUB § 9.4 · 2D AGO</Mono>
      </div>
      <div style={{ marginTop: 20, fontSize: 12.5, color: t.textMuted, textAlign: 'center' }}>No account? <span style={{ color: t.accent, textDecoration: 'underline' }}>Request access</span></div>
    </div>
  </Frame>
);

const AuthMobileSignup = ({ t = T }) => (
  <Frame w={375} h={812} t={t} label="SIGN UP · MOBILE 375">
    <NavMobile t={t} />
    <div style={{ flex: 1, overflow: 'auto', padding: '32px 18px' }}>
      <Mono muted t={t} size={9.5}>REQUEST ACCESS · ABS°</Mono>
      <h1 style={{ fontSize: 36, fontWeight: 800, letterSpacing: '-0.04em', lineHeight: 1, margin: '12px 0 10px' }}>Tell us about your <HL t={t}>project.</HL></h1>
      <p style={{ fontSize: 14, color: t.textMuted, lineHeight: 1.5, margin: '0 0 22px' }}>Private beta, HRM only. We approve in batches.</p>
      <form style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
        <FormField t={t} label="EMAIL" type="email" placeholder="you@firm.com" />
        <FormField t={t} label="NAME" placeholder="Your name" />
        <label style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          <Mono muted t={t} size={9}>YOU ARE A…</Mono>
          <select style={{ padding: '11px 13px', background: t.surface, color: t.text, border: `1.5px solid ${t.text}`, fontFamily: 'inherit', fontSize: 14, outline: 'none' }}>
            <option>Architect</option><option>Homeowner</option><option>Developer</option>
          </select>
        </label>
        <label style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          <Mono muted t={t} size={9}>WHAT ARE YOU WORKING ON?</Mono>
          <textarea rows={3} placeholder="One project, one paragraph." style={{ padding: '11px 13px', background: t.surface, color: t.text, border: `1.5px solid ${t.text}`, fontFamily: 'inherit', fontSize: 14, outline: 'none', resize: 'vertical' }} />
        </label>
        <div style={{ fontSize: 11, color: t.textMuted, lineHeight: 1.45 }}>By requesting access, you agree to our terms. ABS is research, not legal advice.</div>
        <Btn variant="accent" size="lg" t={t} style={{ width: '100%' }}>Request access →</Btn>
      </form>
      <div style={{ marginTop: 20, fontSize: 12.5, color: t.textMuted, textAlign: 'center' }}>Have an account? <span style={{ color: t.accent, textDecoration: 'underline' }}>Log in</span></div>
    </div>
  </Frame>
);

const AuthTabletLogin = ({ t = T }) => (
  <Frame w={768} h={1024} t={t} label="LOG IN · TABLET 768">
    <NavTablet t={t} />
    <div style={{ flex: 1, display: 'grid', gridTemplateColumns: '1fr 1fr', overflow: 'hidden' }}>
      <div style={{ padding: '56px 44px', display: 'flex', flexDirection: 'column', justifyContent: 'center' }}>
        <Mono muted t={t} size={10}>LOG IN · ABS°</Mono>
        <h1 style={{ fontSize: 48, fontWeight: 800, letterSpacing: '-0.04em', lineHeight: 1, margin: '12px 0 12px' }}>Welcome <HL t={t}>back.</HL></h1>
        <p style={{ fontSize: 14.5, color: t.textMuted, lineHeight: 1.55, margin: '0 0 28px' }}>Pick up where you left your last reading.</p>
        <form style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <FormField t={t} label="EMAIL" type="email" placeholder="you@firm.com" />
          <FormField t={t} label="PASSWORD" type="password" placeholder="••••••••" />
          <Btn variant="accent" size="lg" t={t}>Log in →</Btn>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, margin: '4px 0' }}><Hair t={t} style={{ flex: 1 }} /><Mono muted t={t} size={9}>OR</Mono><Hair t={t} style={{ flex: 1 }} /></div>
          <Btn variant="ghost" size="md" t={t}>Continue with Google</Btn>
        </form>
      </div>
      <div style={{ background: t.surfaceAlt, borderLeft: `1px solid ${t.hair}`, padding: '56px 44px', display: 'flex', flexDirection: 'column', justifyContent: 'center' }}>
        <Mono muted t={t} size={10} style={{ marginBottom: 16 }}>FROM YOUR LAST SESSION</Mono>
        <div style={{ background: t.surface, border: `1px solid ${t.hair}`, padding: 20, display: 'flex', flexDirection: 'column', gap: 10 }}>
          <Mono muted t={t} size={9}>5184 MORRIS ST · ER-1</Mono>
          <div style={{ fontSize: 13, color: t.textMuted, fontStyle: 'italic' }}>"Can I add a backyard suite?"</div>
          <div style={{ fontSize: 22, fontWeight: 700, letterSpacing: '-0.025em' }}>Yes — up to 80 m².</div>
          <div style={{ paddingTop: 10, borderTop: `1px solid ${t.hair}`, display: 'flex', justifyContent: 'space-between' }}><Mono muted t={t} size={9}>HRM LUB § 9.4</Mono><Mono muted t={t} size={9}>UPDATED 2D AGO</Mono></div>
        </div>
        <div style={{ marginTop: 16, fontSize: 13, color: t.textMuted, lineHeight: 1.5 }}>Three readings in progress, two awaiting your review.</div>
      </div>
    </div>
  </Frame>
);

const AuthTabletSignup = ({ t = T }) => (
  <Frame w={768} h={1024} t={t} label="SIGN UP · TABLET 768">
    <NavTablet t={t} />
    <div style={{ flex: 1, display: 'grid', gridTemplateColumns: '1fr 1fr', overflow: 'hidden' }}>
      <div style={{ padding: '48px 36px', display: 'flex', flexDirection: 'column', justifyContent: 'center' }}>
        <Mono muted t={t} size={10}>REQUEST ACCESS · ABS°</Mono>
        <h1 style={{ fontSize: 44, fontWeight: 800, letterSpacing: '-0.04em', lineHeight: 1, margin: '12px 0 10px' }}>Tell us about your <HL t={t}>project.</HL></h1>
        <p style={{ fontSize: 14, color: t.textMuted, lineHeight: 1.55, margin: '0 0 22px' }}>Private beta, HRM only. We approve in batches.</p>
        <form style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <FormField t={t} label="EMAIL" type="email" placeholder="you@firm.com" />
          <FormField t={t} label="NAME" placeholder="Your name" />
          <label style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            <Mono muted t={t} size={9}>YOU ARE A…</Mono>
            <select style={{ padding: '11px 13px', background: t.surface, color: t.text, border: `1.5px solid ${t.text}`, fontFamily: 'inherit', fontSize: 14, outline: 'none' }}>
              <option>Architect</option><option>Homeowner</option><option>Developer</option>
            </select>
          </label>
          <Btn variant="accent" size="lg" t={t}>Request access →</Btn>
        </form>
      </div>
      <div style={{ background: t.surfaceAlt, borderLeft: `1px solid ${t.hair}`, padding: '48px 36px', display: 'flex', flexDirection: 'column', justifyContent: 'center', gap: 18 }}>
        <Mono muted t={t} size={10}>WHO USES ABS°</Mono>
        {[['Architects', 'Validate massing studies against zone limits.'], ['Homeowners', 'Confirm an ADU is feasible before hiring.'], ['Developers', 'Pre-acquisition feasibility in seconds.']].map(([r, d]) => (
          <div key={r} style={{ paddingBottom: 14, borderBottom: `1px solid ${t.hair}` }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
              <span style={{ fontSize: 15, fontWeight: 600 }}>{r}</span><Mono accent t={t} size={9}>ACTIVE</Mono>
            </div>
            <div style={{ fontSize: 12.5, color: t.textMuted, lineHeight: 1.5 }}>{d}</div>
          </div>
        ))}
      </div>
    </div>
  </Frame>
);

// ───────────────────────────────────────────────────────────────────
// CANVAS LAYOUT
// ───────────────────────────────────────────────────────────────────

const App = () => (
  <DesignCanvas title="ABS° — Responsive · Mobile 375 + Tablet 768" subtitle="Setback dark theme · primary product surface">
    {/* Intro / decisions */}
    <DCSection id="cx" title="CX decisions" subtitle="The mobile/tablet contract the rest of this canvas implements.">
      <DCArtboard id="cx-card" label="CX · DECISIONS" width={720} height={540}>
        <div style={{ width: '100%', height: '100%', padding: 28, background: T.surface, color: T.text, fontFamily: 'Inter Tight', display: 'flex', flexDirection: 'column', gap: 14, overflow: 'auto' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}><Logo size={24} /><Mono muted size={10}>RESPONSIVE CONTRACT · MAY 2026</Mono></div>
          <h2 style={{ fontSize: 28, fontWeight: 800, letterSpacing: '-0.035em', lineHeight: 1.05, margin: '4px 0 8px' }}>How mobile + tablet diverge from desktop.</h2>
          <div style={{ display: 'grid', gridTemplateColumns: '180px 1fr', rowGap: 14, columnGap: 18, fontSize: 13, lineHeight: 1.5 }}>
            <Mono muted size={9.5}>SIDEBAR · MOBILE</Mono>
            <div>Hamburger top-left of app header. Off-canvas drawer slides in from left over a 50% scrim. <Tag>SWIPE FROM LEFT EDGE</Tag> opens it; tap scrim or ✕ closes.</div>
            <Mono muted size={9.5}>SIDEBAR · TABLET</Mono>
            <div>Always-on rail. 56px icons-only by default; expands to 240px on tap of the rail header. No drawer.</div>
            <Mono muted size={9.5}>PARCEL · MOBILE</Mono>
            <div>Bottom sheet. Triggered by tapping the <strong>address pill</strong> directly under the app header (always visible, also doubles as context indicator). <Tag>SWIPE UP</Tag> from the pill opens it; <Tag>SWIPE DOWN</Tag> on the sheet handle dismisses.</div>
            <Mono muted size={9.5}>PARCEL · TABLET</Mono>
            <div>Side pane (280px). Closed by default to keep the chat readable. <strong>"Parcel" FAB</strong> in the lower-right corner of the chat pane toggles it open.</div>
            <Mono muted size={9.5}>iOS KEYBOARD</Mono>
            <div>Composer is pinned via <code style={{ fontFamily: 'JetBrains Mono', fontSize: 12 }}>position: sticky</code> with <code style={{ fontFamily: 'JetBrains Mono', fontSize: 12 }}>env(safe-area-inset-bottom)</code> padding. When focused, suggestion chips hide; <code style={{ fontFamily: 'JetBrains Mono', fontSize: 12 }}>visualViewport</code> drives the offset above the keyboard. Send button stays reachable.</div>
            <Mono muted size={9.5}>SCROLL</Mono>
            <div>Thread scroll position persists when drawer or sheet opens — they overlay, they don't unmount the thread. Closing returns the user to the exact same scroll offset.</div>
            <Mono muted size={9.5}>ROUTING</Mono>
            <div>Sidebar drawer and parcel sheet are <strong>UI state only</strong>, not URL state. Browser back button never closes them — it navigates back to the previous reading or out of the app. (Confirmed CX choice.)</div>
            <Mono muted size={9.5}>BREAKPOINTS</Mono>
            <div>≤ 639 mobile · 640–1023 tablet · 1024+ desktop.</div>
          </div>
        </div>
      </DCArtboard>
    </DCSection>

    <DCSection id="marketing" title="Marketing home" subtitle="Hero → How it works → Proof grid → Closing CTA at both breakpoints.">
      <DCArtboard id="home-375" label="HOME · 375" width={375} height={2280}><MarketingHome375 /></DCArtboard>
      <DCArtboard id="home-768" label="HOME · 768" width={768} height={2120}><MarketingHome768 /></DCArtboard>
    </DCSection>

    <DCSection id="app-mobile" title="App shell · mobile (375)" subtitle="Three states: default chat, drawer open, parcel bottom sheet.">
      <DCArtboard id="app-m-chat" label="CHAT · SIDEBAR CLOSED" width={375} height={812}><AppMobileChat /></DCArtboard>
      <DCArtboard id="app-m-drawer" label="DRAWER OPEN" width={375} height={812}><AppMobileDrawer /></DCArtboard>
      <DCArtboard id="app-m-sheet" label="PARCEL SHEET" width={375} height={812}><AppMobileSheet /></DCArtboard>
    </DCSection>

    <DCSection id="app-tablet" title="App shell · tablet (768)" subtitle="Always-on icon rail. Parcel pane toggles via FAB.">
      <DCArtboard id="app-t-collapsed" label="ICONS · PARCEL CLOSED" width={768} height={1024}><AppTabletCollapsed /></DCArtboard>
      <DCArtboard id="app-t-expanded" label="EXPANDED · PARCEL CLOSED" width={768} height={1024}><AppTabletExpanded /></DCArtboard>
      <DCArtboard id="app-t-parcel" label="ICONS · PARCEL OPEN" width={768} height={1024}><AppTabletParcel /></DCArtboard>
    </DCSection>

    <DCSection id="auth" title="Auth · sanity check" subtitle="Log in and request access at both breakpoints.">
      <DCArtboard id="auth-m-login" label="LOG IN · 375" width={375} height={812}><AuthMobileLogin /></DCArtboard>
      <DCArtboard id="auth-m-signup" label="SIGN UP · 375" width={375} height={812}><AuthMobileSignup /></DCArtboard>
      <DCArtboard id="auth-t-login" label="LOG IN · 768" width={768} height={1024}><AuthTabletLogin /></DCArtboard>
      <DCArtboard id="auth-t-signup" label="SIGN UP · 768" width={768} height={1024}><AuthTabletSignup /></DCArtboard>
    </DCSection>
  </DesignCanvas>
);

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
