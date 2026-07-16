// Top nav, footer, page shell.

const NAV = [
  { id: 'home', label: 'Home' },
  { id: 'pricing', label: 'Pricing' },
  { id: 'app', label: 'App' },
  { id: 'login', label: 'Log in' },
  { id: 'signup', label: 'Get an invite' },
  { id: 'billing', label: 'Billing' },
];

const TopNav = ({ route, setRoute, mode, setMode }) => {
  const t = useTheme();
  return (
    <header style={{
      position: 'sticky', top: 0, zIndex: 30, background: t.surface,
      borderBottom: `1px solid ${t.hair}`, padding: '14px 32px',
      display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      backdropFilter: 'blur(8px)',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 22 }}>
        <button onClick={() => setRoute('home')} style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}>
          <ABSLogo size={22} />
        </button>
        <span style={{ width: 1, height: 16, background: t.hair }} />
        <Mono muted size={10.5}>HRM · PRIVATE BETA</Mono>
      </div>

      <nav style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
        {NAV.map(n => (
          <button key={n.id} onClick={() => setRoute(n.id)} style={{
            background: 'transparent', border: 'none', cursor: 'pointer',
            padding: '8px 12px', fontFamily: 'inherit', fontSize: 13.5,
            fontWeight: route === n.id ? 600 : 500,
            color: route === n.id ? t.text : t.textMuted,
            letterSpacing: '-0.005em', position: 'relative',
          }}>
            {n.label}
            {route === n.id && <span style={{ position: 'absolute', left: 12, right: 12, bottom: 2, height: 2, background: t.accent }} />}
          </button>
        ))}
      </nav>

      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <ThemeToggle mode={mode} setMode={setMode} />
        <Btn variant="primary" size="sm" onClick={() => setRoute('signup')}>Get an invite →</Btn>
      </div>
    </header>
  );
};

const ThemeToggle = ({ mode, setMode }) => {
  const t = useTheme();
  const isDark = mode === 'dark';
  return (
    <button onClick={() => setMode(isDark ? 'light' : 'dark')} style={{
      display: 'inline-flex', alignItems: 'center', gap: 0,
      background: t.surfaceAlt, border: `1px solid ${t.hair}`,
      padding: 2, cursor: 'pointer', fontFamily: 'JetBrains Mono', fontSize: 10,
      letterSpacing: '0.12em', textTransform: 'uppercase',
    }} title="Toggle light / dark">
      <span style={{
        padding: '5px 10px', background: isDark ? 'transparent' : t.text,
        color: isDark ? t.textMuted : t.surface, transition: 'all 0.15s',
      }}>04</span>
      <span style={{
        padding: '5px 10px', background: isDark ? t.text : 'transparent',
        color: isDark ? t.surface : t.textMuted, transition: 'all 0.15s',
      }}>03</span>
    </button>
  );
};

const Footer = () => {
  const t = useTheme();
  return (
    <footer style={{ borderTop: `1px solid ${t.hair}`, padding: '36px 32px 28px', display: 'flex', flexDirection: 'column', gap: 22, marginTop: 80 }}>
      <div style={{ display: 'grid', gridTemplateColumns: '1.4fr 1fr 1fr 1fr', gap: 28 }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          <ABSLogo size={28} />
          <div style={{ fontSize: 13, color: t.textMuted, lineHeight: 1.5, maxWidth: 280 }}>
            An expert planner integrated into your workflow. Built in Halifax.
          </div>
        </div>
        {[
          { h: 'Product', items: ['Home', 'Pricing', 'Changelog', 'Coverage'] },
          { h: 'Account', items: ['Log in', 'Get an invite', 'Billing', 'Support'] },
          { h: 'Company', items: ['About', 'Privacy', 'Terms', 'hello@abs.app'] },
        ].map(c => (
          <div key={c.h} style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <Mono muted size={10}>{c.h}</Mono>
            {c.items.map(i => <span key={i} style={{ fontSize: 13, color: t.text, cursor: 'pointer' }}>{i}</span>)}
          </div>
        ))}
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', paddingTop: 18, borderTop: `1px solid ${t.hair}` }}>
        <Mono muted size={9.5}>© 2026 ABS · HALIFAX REGIONAL MUNICIPALITY</Mono>
        <Mono muted size={9.5}>NOT LEGAL ADVICE · VERIFY WITH HRM PLANNING</Mono>
      </div>
    </footer>
  );
};

const PageShell = ({ children }) => {
  const t = useTheme();
  return <div style={{ minHeight: '100vh', background: t.surface, color: t.text }}>{children}</div>;
};

Object.assign(window, { TopNav, Footer, PageShell, NAV });
