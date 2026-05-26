// Pricing, Log-in, Sign-up, Billing.

const Page = ({ children, narrow = false }) => {
  const t = useTheme();
  return (
    <div style={{ padding: '56px 32px', maxWidth: narrow ? 680 : 1200, margin: '0 auto', minHeight: 'calc(100vh - 200px)' }}>
      {children}
    </div>
  );
};

const PageHead = ({ kicker, title, sub }) => {
  const t = useTheme();
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14, marginBottom: 40, paddingBottom: 28, borderBottom: `1px solid ${t.hair}` }}>
      <Mono muted size={11}>{kicker}</Mono>
      <h1 style={{ fontSize: 56, fontWeight: 800, letterSpacing: '-0.04em', lineHeight: 0.98, margin: 0 }}>{title}</h1>
      {sub && <p style={{ fontSize: 17, color: t.textMuted, lineHeight: 1.45, margin: 0, maxWidth: 620 }}>{sub}</p>}
    </div>
  );
};

// — PRICING
const PricingPage = ({ setRoute }) => {
  const t = useTheme();
  const TIERS = [
    {
      name: 'Drafter',
      desc: 'For homeowners and small projects.',
      price: '$24',
      cadence: '/ month',
      features: [
        '50 readings / month',
        '1 saved parcel',
        'Plain-language verdicts',
        'Sourced citations',
        'Email support',
      ],
      cta: 'Start a project',
      featured: false,
    },
    {
      name: 'Practice',
      desc: 'For architects and design firms.',
      price: '$180',
      cadence: '/ seat / month',
      features: [
        'Unlimited readings',
        'Unlimited parcels',
        'Permit-ready exports',
        'Reading history & versioning',
        'Team workspace (up to 10 seats)',
        'Priority support',
      ],
      cta: 'Get an invite',
      featured: true,
    },
    {
      name: 'Developer',
      desc: 'For development teams and consultants.',
      price: 'Custom',
      cadence: '',
      features: [
        'Everything in Practice',
        'API access',
        'Bulk parcel analysis',
        'Custom reporting',
        'SSO + audit logs',
        'Dedicated planner liaison',
      ],
      cta: 'Talk to us',
      featured: false,
    },
  ];

  return (
    <Page>
      <PageHead
        kicker="PRICING · HRM PRIVATE BETA"
        title="Three tiers. Same agent."
        sub="Beta pricing. Locks for the first year on any plan started before public launch. All prices in CAD."
      />

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 14 }}>
        {TIERS.map(tier => (
          <div key={tier.name} style={{
            background: tier.featured ? t.text : t.surface,
            color: tier.featured ? t.surface : t.text,
            border: tier.featured ? 'none' : `1.5px solid ${t.text}`,
            padding: 28, display: 'flex', flexDirection: 'column', gap: 22, minHeight: 540,
            position: 'relative',
          }}>
            {tier.featured && (
              <div style={{ position: 'absolute', top: -1, right: -1, background: t.accent, color: t.onAccent, padding: '5px 10px', fontFamily: 'JetBrains Mono', fontSize: 9.5, letterSpacing: '0.14em' }}>
                MOST POPULAR
              </div>
            )}

            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              <Mono size={11} style={{ color: tier.featured ? t.surface : t.textMuted }}>TIER · {tier.name.toUpperCase()}</Mono>
              <div style={{ fontSize: 32, fontWeight: 700, letterSpacing: '-0.03em', lineHeight: 1 }}>{tier.name}</div>
              <div style={{ fontSize: 13.5, color: tier.featured ? 'rgba(255,255,255,0.65)' : t.textMuted, lineHeight: 1.4 }}>{tier.desc}</div>
            </div>

            <div style={{ display: 'flex', alignItems: 'baseline', gap: 6, paddingBottom: 18, borderBottom: `1px solid ${tier.featured ? 'rgba(255,255,255,0.15)' : t.hair}` }}>
              <span style={{ fontSize: 56, fontWeight: 800, letterSpacing: '-0.04em', lineHeight: 1 }}>{tier.price}</span>
              <span style={{ fontSize: 14, color: tier.featured ? 'rgba(255,255,255,0.6)' : t.textMuted }}>{tier.cadence}</span>
            </div>

            <ul style={{ listStyle: 'none', padding: 0, margin: 0, display: 'flex', flexDirection: 'column', gap: 10, flex: 1 }}>
              {tier.features.map(f => (
                <li key={f} style={{ display: 'flex', alignItems: 'flex-start', gap: 10, fontSize: 13.5, lineHeight: 1.45 }}>
                  <span style={{ color: t.accentInk, fontFamily: 'JetBrains Mono', fontSize: 11, paddingTop: 1 }}>+</span>
                  <span>{f}</span>
                </li>
              ))}
            </ul>

            <Btn
              variant={tier.featured ? 'accent' : 'primary'}
              onClick={() => setRoute('signup')}
              style={tier.featured ? {} : {}}
            >{tier.cta} →</Btn>
          </div>
        ))}
      </div>

      <div style={{ marginTop: 56, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 18 }}>
        <FAQ q="What counts as a reading?" a="One question against one parcel. Follow-ups in the same conversation are free." />
        <FAQ q="Can I cancel anytime?" a="Yes. Monthly plans cancel with one click. No call, no email." />
        <FAQ q="What jurisdictions are supported?" a="Halifax Regional Municipality only, during private beta. We're adding Atlantic Canada cities through 2026." />
        <FAQ q="Is this legal advice?" a="No. ABS is research, not legal advice. Always verify with HRM Planning before submitting permits." />
      </div>
    </Page>
  );
};

const FAQ = ({ q, a }) => {
  const t = useTheme();
  return (
    <div style={{ padding: '20px 22px', background: t.surfaceAlt, border: `1px solid ${t.hair}` }}>
      <div style={{ fontSize: 15, fontWeight: 600, letterSpacing: '-0.01em', marginBottom: 6 }}>{q}</div>
      <div style={{ fontSize: 13.5, color: t.textMuted, lineHeight: 1.5 }}>{a}</div>
    </div>
  );
};

// — AUTH (login + signup share form chrome)

const AuthShell = ({ kicker, title, sub, children, side }) => {
  const t = useTheme();
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', minHeight: 'calc(100vh - 70px)' }}>
      <div style={{ padding: '64px 56px', display: 'flex', flexDirection: 'column', justifyContent: 'center', maxWidth: 560, margin: '0 auto', width: '100%' }}>
        <Mono muted size={11} style={{ marginBottom: 14 }}>{kicker}</Mono>
        <h1 style={{ fontSize: 48, fontWeight: 800, letterSpacing: '-0.04em', lineHeight: 1, margin: '0 0 12px' }}>{title}</h1>
        {sub && <p style={{ fontSize: 15, color: t.textMuted, lineHeight: 1.5, margin: '0 0 32px' }}>{sub}</p>}
        {children}
      </div>
      <div style={{ background: t.surfaceAlt, borderLeft: `1px solid ${t.hair}`, padding: '64px 56px', display: 'flex', flexDirection: 'column', justifyContent: 'center' }}>
        {side}
      </div>
    </div>
  );
};

const Field = ({ label, type = 'text', placeholder, value, onChange, hint }) => {
  const t = useTheme();
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <Mono muted size={10}>{label}</Mono>
      <input type={type} placeholder={placeholder} value={value || ''} onChange={onChange} style={{
        padding: '12px 14px', background: t.surface, color: t.text,
        border: `1.5px solid ${t.text}`, fontFamily: 'inherit', fontSize: 14, outline: 'none',
        letterSpacing: '-0.005em',
      }} />
      {hint && <span style={{ fontSize: 11.5, color: t.textMuted }}>{hint}</span>}
    </label>
  );
};

const TextArea = ({ label, placeholder, value, onChange, rows = 4 }) => {
  const t = useTheme();
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <Mono muted size={10}>{label}</Mono>
      <textarea placeholder={placeholder} value={value || ''} onChange={onChange} rows={rows} style={{
        padding: '12px 14px', background: t.surface, color: t.text,
        border: `1.5px solid ${t.text}`, fontFamily: 'inherit', fontSize: 14, outline: 'none',
        letterSpacing: '-0.005em', resize: 'vertical',
      }} />
    </label>
  );
};

const Select = ({ label, options, value, onChange }) => {
  const t = useTheme();
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <Mono muted size={10}>{label}</Mono>
      <select value={value} onChange={onChange} style={{
        padding: '12px 14px', background: t.surface, color: t.text,
        border: `1.5px solid ${t.text}`, fontFamily: 'inherit', fontSize: 14, outline: 'none',
      }}>
        {options.map(o => <option key={o} value={o}>{o}</option>)}
      </select>
    </label>
  );
};

const LoginPage = ({ setRoute }) => {
  const t = useTheme();
  const [email, setEmail] = React.useState('');
  const [pw, setPw] = React.useState('');

  return (
    <AuthShell
      kicker="LOG IN · ABS"
      title="Welcome back."
      sub="Pick up where you left your last reading."
      side={
        <div style={{ display: 'flex', flexDirection: 'column', gap: 18, maxWidth: 420 }}>
          <Mono muted size={11}>FROM YOUR LAST SESSION</Mono>
          <div style={{ background: t.surface, border: `1px solid ${t.hair}`, padding: 22, display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
              <Mono muted size={10}>5184 MORRIS ST · ER-1</Mono>
              <Mono accent size={10}>OPEN</Mono>
            </div>
            <div style={{ fontSize: 13, color: t.textMuted, fontStyle: 'italic' }}>"Can I add a backyard suite?"</div>
            <div style={{ fontSize: 22, fontWeight: 800, letterSpacing: '-0.03em', lineHeight: 1.15 }}>
              <HighlightWord>Yes — up to 80 m².</HighlightWord>
            </div>
            <div style={{ paddingTop: 10, borderTop: `1px solid ${t.hair}`, display: 'flex', justifyContent: 'space-between' }}>
              <Mono muted size={9.5}>HRM LUB § 9.4</Mono>
              <Mono muted size={9.5}>UPDATED 2 DAYS AGO</Mono>
            </div>
          </div>
          <div style={{ fontSize: 13, color: t.textMuted, lineHeight: 1.5 }}>
            Three readings in progress, two awaiting your review.
          </div>
        </div>
      }
    >
      <form onSubmit={e => { e.preventDefault(); setRoute('app'); }} style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        <Field label="EMAIL" type="email" placeholder="you@firm.com" value={email} onChange={e => setEmail(e.target.value)} />
        <Field label="PASSWORD" type="password" placeholder="••••••••" value={pw} onChange={e => setPw(e.target.value)} />
        <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
          <button type="button" style={{ background: 'none', border: 'none', color: t.textMuted, fontSize: 12, cursor: 'pointer', fontFamily: 'inherit' }}>Forgot password?</button>
        </div>
        <Btn variant="primary" size="lg" type="submit">Log in →</Btn>

        <div style={{ display: 'flex', alignItems: 'center', gap: 12, margin: '8px 0' }}>
          <div style={{ flex: 1, height: 1, background: t.hair }} />
          <Mono muted size={9.5}>OR</Mono>
          <div style={{ flex: 1, height: 1, background: t.hair }} />
        </div>

        <Btn variant="ghost" size="lg" type="button">Continue with Google</Btn>
        <Btn variant="ghost" size="lg" type="button">Continue with magic link</Btn>

        <div style={{ marginTop: 14, fontSize: 13, color: t.textMuted }}>
          Don't have an account? <button onClick={() => setRoute('signup')} type="button" style={{ background: 'none', border: 'none', color: t.text, textDecoration: 'underline', cursor: 'pointer', fontSize: 13, fontFamily: 'inherit', padding: 0 }}>Request an invite</button>
        </div>
      </form>
    </AuthShell>
  );
};

const SignupPage = ({ setRoute }) => {
  const t = useTheme();
  const [submitted, setSubmitted] = React.useState(false);
  const [data, setData] = React.useState({
    email: '', name: '', role: 'Architect', project: '',
  });
  const set = (k) => (e) => setData(d => ({ ...d, [k]: e.target.value }));

  if (submitted) {
    return (
      <AuthShell
        kicker="REQUEST RECEIVED"
        title="We'll be in touch."
        sub="Most invites go out within 48 hours during private beta. We review every request to make sure ABS is the right fit for your project."
        side={
          <div style={{ display: 'flex', flexDirection: 'column', gap: 18, maxWidth: 420 }}>
            <Mono muted size={11}>WHAT HAPPENS NEXT</Mono>
            {[
              { n: '01', t: 'A planner reviews your request', d: 'We confirm your project is in HRM and ABS can help.' },
              { n: '02', t: 'You get an invite link by email', d: 'Within 48 hours during business days.' },
              { n: '03', t: 'You start reading', d: 'Set up takes about 90 seconds. First parcel on us.' },
            ].map(s => (
              <div key={s.n} style={{ display: 'flex', gap: 14 }}>
                <Mono accent size={11} style={{ minWidth: 24 }}>{s.n}</Mono>
                <div>
                  <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 3 }}>{s.t}</div>
                  <div style={{ fontSize: 12.5, color: t.textMuted, lineHeight: 1.45 }}>{s.d}</div>
                </div>
              </div>
            ))}
          </div>
        }
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
          <div style={{ background: t.accent, color: t.onAccent, padding: 22, display: 'flex', flexDirection: 'column', gap: 8 }}>
            <Mono size={10} style={{ color: t.onAccent }}>CONFIRMATION · #ABS-{Math.floor(Math.random() * 9000 + 1000)}</Mono>
            <div style={{ fontSize: 22, fontWeight: 800, letterSpacing: '-0.03em', lineHeight: 1.1 }}>You're on the list.</div>
            <div style={{ fontSize: 13, lineHeight: 1.45 }}>We've sent a copy to {data.email || 'your inbox'}.</div>
          </div>
          <Btn variant="ghost" size="lg" onClick={() => setRoute('home')}>Back to home</Btn>
        </div>
      </AuthShell>
    );
  }

  return (
    <AuthShell
      kicker="GET AN INVITE · ABS"
      title="Tell us about your project."
      sub="Private beta, HRM only. We approve invites in batches based on project fit."
      side={
        <div style={{ display: 'flex', flexDirection: 'column', gap: 22, maxWidth: 440 }}>
          <Mono muted size={11}>WHO USES ABS</Mono>
          {[
            { r: 'Architects', d: 'Validate massing studies against zone limits before drawing.' },
            { r: 'Homeowners', d: 'Confirm an ADU or addition is feasible before hiring an architect.' },
            { r: 'Developers', d: 'Pre-acquisition feasibility. By-right capacity in seconds.' },
          ].map(x => (
            <div key={x.r} style={{ paddingBottom: 16, borderBottom: `1px solid ${t.hair}` }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 4 }}>
                <span style={{ fontSize: 17, fontWeight: 700, letterSpacing: '-0.02em' }}>{x.r}</span>
                <Mono accent size={10}>ACTIVE</Mono>
              </div>
              <div style={{ fontSize: 13, color: t.textMuted, lineHeight: 1.45 }}>{x.d}</div>
            </div>
          ))}
        </div>
      }
    >
      <form onSubmit={e => { e.preventDefault(); setSubmitted(true); }} style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        <Field label="EMAIL" type="email" placeholder="you@firm.com" value={data.email} onChange={set('email')} />
        <Field label="NAME" placeholder="Your name" value={data.name} onChange={set('name')} />
        <Select label="YOU ARE A…" options={['Architect', 'Homeowner', 'Developer', 'Planner / consultant', 'Other']} value={data.role} onChange={set('role')} />
        <TextArea label="WHAT ARE YOU WORKING ON?" placeholder="One project, one paragraph. The address or zone is helpful." value={data.project} onChange={set('project')} rows={4} />
        <div style={{ fontSize: 12, color: t.textMuted, lineHeight: 1.45 }}>
          By requesting an invite, you agree to our terms and acknowledge ABS is research, not legal advice.
        </div>
        <Btn variant="primary" size="lg" type="submit">Request invite →</Btn>
        <div style={{ marginTop: 4, fontSize: 13, color: t.textMuted }}>
          Already have an account? <button onClick={() => setRoute('login')} type="button" style={{ background: 'none', border: 'none', color: t.text, textDecoration: 'underline', cursor: 'pointer', fontSize: 13, fontFamily: 'inherit', padding: 0 }}>Log in</button>
        </div>
      </form>
    </AuthShell>
  );
};

// — BILLING

const BillingPage = ({ setRoute }) => {
  const t = useTheme();
  const INVOICES = [
    { id: 'INV-2026-0421', date: '2026-04-30', plan: 'Practice · 4 seats', amount: '$720.00', status: 'PAID' },
    { id: 'INV-2026-0398', date: '2026-03-30', plan: 'Practice · 4 seats', amount: '$720.00', status: 'PAID' },
    { id: 'INV-2026-0367', date: '2026-02-28', plan: 'Practice · 3 seats', amount: '$540.00', status: 'PAID' },
    { id: 'INV-2026-0341', date: '2026-01-30', plan: 'Practice · 3 seats', amount: '$540.00', status: 'PAID' },
    { id: 'INV-2025-0322', date: '2025-12-30', plan: 'Practice · 2 seats', amount: '$360.00', status: 'PAID' },
  ];

  return (
    <Page>
      <PageHead
        kicker="ACCOUNT · BILLING"
        title="Billing."
        sub="Halifax Studio Co. · Practice plan · 4 seats. Invoices below; export anytime."
      />

      {/* Plan card */}
      <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 14, marginBottom: 36 }}>
        <div style={{ background: t.text, color: t.surface, padding: 28, display: 'flex', flexDirection: 'column', gap: 18 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
            <div>
              <Mono size={10} style={{ color: 'rgba(255,255,255,0.6)' }}>CURRENT PLAN</Mono>
              <div style={{ fontSize: 36, fontWeight: 800, letterSpacing: '-0.035em', marginTop: 6 }}>Practice</div>
              <div style={{ fontSize: 13, color: 'rgba(255,255,255,0.7)', marginTop: 4 }}>$180 / seat / month · billed monthly</div>
            </div>
            <span style={{ background: t.accent, color: t.onAccent, padding: '4px 10px', fontFamily: 'JetBrains Mono', fontSize: 9.5, letterSpacing: '0.14em' }}>ACTIVE</span>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 24, paddingTop: 18, borderTop: '1px solid rgba(255,255,255,0.15)' }}>
            <div>
              <Mono size={9.5} style={{ color: 'rgba(255,255,255,0.55)' }}>SEATS</Mono>
              <div style={{ fontSize: 26, fontWeight: 700, letterSpacing: '-0.025em', marginTop: 4 }}>4 / 10</div>
            </div>
            <div>
              <Mono size={9.5} style={{ color: 'rgba(255,255,255,0.55)' }}>READINGS · MAY</Mono>
              <div style={{ fontSize: 26, fontWeight: 700, letterSpacing: '-0.025em', marginTop: 4 }}>247</div>
            </div>
            <div>
              <Mono size={9.5} style={{ color: 'rgba(255,255,255,0.55)' }}>NEXT INVOICE</Mono>
              <div style={{ fontSize: 26, fontWeight: 700, letterSpacing: '-0.025em', marginTop: 4 }}>May 30</div>
            </div>
          </div>

          <div style={{ display: 'flex', gap: 10, marginTop: 6 }}>
            <Btn variant="accent" size="sm">Manage seats</Btn>
            <Btn variant="ghost" size="sm" style={{ borderColor: 'rgba(255,255,255,0.3)', color: t.surface }}>Change plan</Btn>
          </div>
        </div>

        <div style={{ background: t.surfaceAlt, border: `1px solid ${t.hair}`, padding: 24, display: 'flex', flexDirection: 'column', gap: 14 }}>
          <Mono muted size={10}>PAYMENT METHOD</Mono>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <div style={{ width: 44, height: 30, background: t.text, color: t.surface, display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: 'JetBrains Mono', fontSize: 9, letterSpacing: '0.06em' }}>VISA</div>
            <div style={{ display: 'flex', flexDirection: 'column' }}>
              <span style={{ fontSize: 14, fontWeight: 600 }}>•••• •••• •••• 4421</span>
              <span style={{ fontSize: 12, color: t.textMuted }}>Expires 11/28</span>
            </div>
          </div>
          <Btn variant="ghost" size="sm">Update card</Btn>
          <div style={{ paddingTop: 14, borderTop: `1px solid ${t.hair}`, display: 'flex', flexDirection: 'column', gap: 6 }}>
            <Mono muted size={10}>BILLING EMAIL</Mono>
            <span style={{ fontSize: 13 }}>billing@halifaxstudio.co</span>
          </div>
        </div>
      </div>

      {/* Invoices */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14 }}>
        <Mono muted size={11}>INVOICE HISTORY</Mono>
        <Btn variant="quiet" size="sm">Export all (.csv)</Btn>
      </div>
      <div style={{ border: `1px solid ${t.hair}` }}>
        <div style={{ display: 'grid', gridTemplateColumns: '1.5fr 1fr 2fr 1fr 1fr 0.5fr', gap: 16, padding: '12px 18px', background: t.surfaceAlt, borderBottom: `1px solid ${t.hair}` }}>
          {['INVOICE', 'DATE', 'DESCRIPTION', 'AMOUNT', 'STATUS', ''].map(h => <Mono muted size={9.5} key={h}>{h}</Mono>)}
        </div>
        {INVOICES.map((inv, i) => (
          <div key={inv.id} style={{ display: 'grid', gridTemplateColumns: '1.5fr 1fr 2fr 1fr 1fr 0.5fr', gap: 16, padding: '14px 18px', borderBottom: i < INVOICES.length - 1 ? `1px solid ${t.hair}` : 'none', alignItems: 'center', fontSize: 13 }}>
            <span style={{ fontFamily: 'JetBrains Mono', fontSize: 12 }}>{inv.id}</span>
            <span style={{ color: t.textMuted }}>{inv.date}</span>
            <span>{inv.plan}</span>
            <span style={{ fontWeight: 600, letterSpacing: '-0.01em' }}>{inv.amount}</span>
            <span><Mono accent size={9.5}>{inv.status}</Mono></span>
            <button style={{ background: 'transparent', border: 'none', color: t.textMuted, cursor: 'pointer', fontFamily: 'JetBrains Mono', fontSize: 10, letterSpacing: '0.08em', textAlign: 'right' }}>PDF ↓</button>
          </div>
        ))}
      </div>

      {/* Usage */}
      <div style={{ marginTop: 36, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
        <div style={{ background: t.surfaceAlt, border: `1px solid ${t.hair}`, padding: 24 }}>
          <Mono muted size={10}>USAGE · MAY 2026</Mono>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginTop: 8, marginBottom: 14 }}>
            <span style={{ fontSize: 38, fontWeight: 800, letterSpacing: '-0.035em' }}>247</span>
            <span style={{ fontSize: 13, color: t.textMuted }}>readings · unlimited</span>
          </div>
          <div style={{ display: 'flex', gap: 2, height: 32 }}>
            {Array.from({ length: 30 }).map((_, i) => {
              const h = 30 + Math.sin(i * 0.6) * 12 + Math.random() * 18;
              return <div key={i} style={{ flex: 1, background: i > 26 ? t.accent : t.text, opacity: i > 26 ? 1 : 0.3, height: `${h}%`, alignSelf: 'flex-end' }} />;
            })}
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 8 }}>
            <Mono muted size={9}>MAY 1</Mono>
            <Mono muted size={9}>MAY 30</Mono>
          </div>
        </div>

        <div style={{ background: t.surfaceAlt, border: `1px solid ${t.hair}`, padding: 24, display: 'flex', flexDirection: 'column', gap: 12 }}>
          <Mono muted size={10}>TOP PARCELS · THIS MONTH</Mono>
          {[
            { addr: '5184 Morris St', n: 42 },
            { addr: '1208 Robie St', n: 38 },
            { addr: '17 Edward St', n: 24 },
            { addr: '2310 Gottingen St', n: 19 },
          ].map(p => (
            <div key={p.addr} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', paddingBottom: 8, borderBottom: `1px solid ${t.hair}` }}>
              <span style={{ fontSize: 13.5 }}>{p.addr}</span>
              <span style={{ fontFamily: 'JetBrains Mono', fontSize: 12, color: t.textMuted }}>{p.n} readings</span>
            </div>
          ))}
        </div>
      </div>
    </Page>
  );
};

Object.assign(window, { PricingPage, LoginPage, SignupPage, BillingPage });
