// Consistent authorized navigation.
// One workspace menu + one top bar, shared across every signed-in surface
// (#app, #billing, and reference pages once authed). Replaces the per-page
// top-right buttons + ad-hoc back buttons with a single repeatable control.

// Destinations reachable from the authorized menu.
const AUTH_AREA = ['app', 'newcase', 'billing', 'coverage', 'changelog', 'support'];

const AUTH_NAV = {
  WORKSPACE: [
    { id: 'newcase', label: 'Open a case', hint: 'Start a new reading' },
    { id: 'app', label: 'Readings', hint: 'Chat + parcel readings' },
    { id: 'billing', label: 'Billing', hint: 'Plan, seats & invoices' },
  ],
  REFERENCE: [
    { id: 'coverage', label: 'Coverage', hint: 'Indexed jurisdictions' },
    { id: 'changelog', label: 'Changelog', hint: 'Release register' },
    { id: 'support', label: 'Support', hint: 'Help & contact' },
  ],
};

const AUTH_LABELS = {
  app: 'WORKSPACE · READINGS',
  newcase: 'ACCOUNT · NEW CASE',
  billing: 'ACCOUNT · BILLING',
  coverage: 'REFERENCE · COVERAGE',
  changelog: 'REFERENCE · CHANGELOG',
  support: 'REFERENCE · SUPPORT',
};

// — Shared bits ----------------------------------------------------------

const Avatar = ({ size = 26 }) => {
  const t = useTheme();
  return (
    <div style={{
      width: size, height: size, flexShrink: 0, background: t.text, color: t.surface,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      fontFamily: 'JetBrains Mono', fontSize: size * 0.4, fontWeight: 600, letterSpacing: '0.02em',
    }}>HS</div>
  );
};

const ThemeSwitch = ({ mode, setMode, size = 'sm' }) => {
  const t = useTheme();
  const isDark = mode === 'dark';
  const pad = size === 'sm' ? '4px 9px' : '5px 10px';
  return (
    <button onClick={() => setMode(isDark ? 'light' : 'dark')} title="Blueprint / Setback" style={{
      display: 'inline-flex', alignItems: 'center', background: t.surface, border: `1px solid ${t.hair}`,
      padding: 2, cursor: 'pointer', fontFamily: 'JetBrains Mono', fontSize: 9.5,
      letterSpacing: '0.12em', textTransform: 'uppercase',
    }}>
      <span style={{ padding: pad, background: isDark ? 'transparent' : t.text, color: isDark ? t.textMuted : t.surface, transition: 'all .15s' }}>04</span>
      <span style={{ padding: pad, background: isDark ? t.text : 'transparent', color: isDark ? t.surface : t.textMuted, transition: 'all .15s' }}>03</span>
    </button>
  );
};

const MenuRow = ({ item, active, onClick }) => {
  const t = useTheme();
  const [hover, setHover] = React.useState(false);
  return (
    <button
      onClick={onClick}
      onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}
      style={{
        display: 'grid', gridTemplateColumns: '1fr auto', alignItems: 'center', gap: 10,
        width: '100%', textAlign: 'left', cursor: 'pointer', fontFamily: 'inherit',
        background: active || hover ? t.surfaceAlt : 'transparent',
        border: 'none', borderLeft: `2px solid ${active ? t.accent : 'transparent'}`,
        padding: '9px 14px 9px 13px', transition: 'background .1s',
      }}>
      <span style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
        <span style={{ fontSize: 13.5, fontWeight: active ? 600 : 500, letterSpacing: '-0.01em', color: t.text }}>{item.label}</span>
        <span style={{ fontSize: 11, color: t.textMuted, letterSpacing: '-0.005em' }}>{item.hint}</span>
      </span>
      {active
        ? <Mono accent size={9}>OPEN</Mono>
        : <span style={{ color: t.textMuted, opacity: hover ? 1 : 0, transition: 'opacity .12s' }}>→</span>}
    </button>
  );
};

// — The workspace menu (the consistent navigation control) ----------------

const AccountMenu = ({ route, setRoute, mode, setMode, onLogout, compact = false, showNav = true }) => {
  const t = useTheme();
  const [open, setOpen] = React.useState(false);
  const ref = React.useRef(null);

  React.useEffect(() => {
    if (!open) return;
    const onDown = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('mousedown', onDown); document.removeEventListener('keydown', onKey); };
  }, [open]);

  const nav = (id) => { setOpen(false); setRoute(id); };

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      {/* Trigger */}
      <button onClick={() => setOpen(o => !o)} style={{
        display: 'inline-flex', alignItems: 'center', gap: 9,
        background: t.surface, border: `1px solid ${open ? t.text : t.hair}`,
        padding: compact ? '4px 8px 4px 4px' : '4px 12px 4px 4px', cursor: 'pointer',
        fontFamily: 'inherit', transition: 'border-color .12s',
      }}>
        <Avatar size={compact ? 24 : 26} />
        {!compact && (
          <span style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-start', lineHeight: 1.15 }}>
            <span style={{ fontSize: 12.5, fontWeight: 600, color: t.text, letterSpacing: '-0.01em' }}>Halifax Studio</span>
            <span style={{ fontFamily: 'JetBrains Mono', fontSize: 9, color: t.textMuted, letterSpacing: '0.08em' }}>PRACTICE · 4 SEATS</span>
          </span>
        )}
        <span style={{ color: t.textMuted, fontSize: 10, transform: open ? 'rotate(180deg)' : 'none', transition: 'transform .15s', marginLeft: compact ? 0 : 2 }}>▾</span>
      </button>

      {/* Panel */}
      {open && (
        <div style={{
          position: 'absolute', top: 'calc(100% + 8px)', right: 0, width: 278, zIndex: 60,
          background: t.surface, border: `1.5px solid ${t.text}`,
          display: 'flex', flexDirection: 'column',
        }}>
          {/* Workspace identity */}
          <div style={{ padding: '14px 14px', display: 'flex', alignItems: 'center', gap: 11, borderBottom: `1px solid ${t.hair}` }}>
            <Avatar size={34} />
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: 14, fontWeight: 600, letterSpacing: '-0.01em' }}>Halifax Studio</div>
              <div style={{ fontSize: 11.5, color: t.textMuted }}>billing@halifaxstudio.co</div>
            </div>
            <span style={{ background: t.accent, color: t.onAccent, padding: '3px 7px', fontFamily: 'JetBrains Mono', fontSize: 8.5, letterSpacing: '0.12em' }}>PRACTICE</span>
          </div>

          {showNav && Object.entries(AUTH_NAV).map(([section, items]) => (
            <div key={section} style={{ padding: '10px 0 8px', borderBottom: `1px solid ${t.hair}` }}>
              <Mono muted size={9} style={{ padding: '0 14px 6px', display: 'block' }}>{section}</Mono>
              {items.map(it => (
                <MenuRow key={it.id} item={it} active={route === it.id} onClick={() => nav(it.id)} />
              ))}
            </div>
          ))}

          {/* Appearance */}
          <div style={{ padding: '11px 14px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', borderBottom: `1px solid ${t.hair}` }}>
            <Mono muted size={9}>APPEARANCE</Mono>
            <ThemeSwitch mode={mode} setMode={setMode} />
          </div>

          {/* Log out */}
          <button onClick={() => { setOpen(false); onLogout && onLogout(); }} style={{
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            padding: '12px 14px', background: 'transparent', border: 'none', cursor: 'pointer',
            fontFamily: 'inherit', fontSize: 13, fontWeight: 500, color: t.text, textAlign: 'left',
          }}
            onMouseEnter={e => e.currentTarget.style.background = t.surfaceAlt}
            onMouseLeave={e => e.currentTarget.style.background = 'transparent'}>
            <span>Log out</span>
            <span style={{ color: t.textMuted }}>→</span>
          </button>
        </div>
      )}
    </div>
  );
};

// — Authorized top bar (non-app pages) -----------------------------------

const AuthBar = ({ route, setRoute, mode, setMode, onLogout, navStyle = 'menu' }) => {
  const t = useTheme();
  const ALL = [...AUTH_NAV.WORKSPACE, ...AUTH_NAV.REFERENCE];

  return (
    <header style={{
      position: 'sticky', top: 0, zIndex: 30, background: t.surface,
      borderBottom: `1px solid ${t.hair}`, padding: '11px 32px',
      display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 20,
      backdropFilter: 'blur(8px)',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 18, minWidth: 0 }}>
        <button onClick={() => setRoute('app')} title="Workspace" style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}>
          <ABSLogo size={20} />
        </button>
        <span style={{ width: 1, height: 16, background: t.hair }} />
        {navStyle === 'tabs' ? (
          <nav style={{ display: 'flex', alignItems: 'center', gap: 2 }}>
            {ALL.map(n => {
              const active = route === n.id;
              return (
                <button key={n.id} onClick={() => setRoute(n.id)} style={{
                  background: 'transparent', border: 'none', cursor: 'pointer',
                  padding: '8px 12px', fontFamily: 'inherit', fontSize: 13,
                  fontWeight: active ? 600 : 500, color: active ? t.text : t.textMuted,
                  letterSpacing: '-0.005em', position: 'relative',
                }}>
                  {n.label}
                  {active && <span style={{ position: 'absolute', left: 12, right: 12, bottom: 1, height: 2, background: t.accent }} />}
                </button>
              );
            })}
          </nav>
        ) : (
          <Mono muted size={10}>{AUTH_LABELS[route] || 'WORKSPACE'}</Mono>
        )}
      </div>

      <AccountMenu
        route={route} setRoute={setRoute} mode={mode} setMode={setMode} onLogout={onLogout}
        showNav={navStyle !== 'tabs'}
      />
    </header>
  );
};

// — Slim authorized footer (replaces the heavy marketing footer) ----------

const AuthFooter = ({ setRoute }) => {
  const t = useTheme();
  return (
    <footer style={{
      borderTop: `1px solid ${t.hair}`, marginTop: 64, padding: '18px 32px',
      display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 18 }}>
        <Mono muted size={9.5}>© 2026 ABS · HALIFAX STUDIO</Mono>
        <button onClick={() => setRoute('support')} style={{ background: 'none', border: 'none', padding: 0, cursor: 'pointer', fontFamily: 'JetBrains Mono', fontSize: 9.5, letterSpacing: '0.14em', textTransform: 'uppercase', color: t.textMuted }}>SUPPORT</button>
        <button onClick={() => setRoute('changelog')} style={{ background: 'none', border: 'none', padding: 0, cursor: 'pointer', fontFamily: 'JetBrains Mono', fontSize: 9.5, letterSpacing: '0.14em', textTransform: 'uppercase', color: t.textMuted }}>CHANGELOG</button>
      </div>
      <Mono muted size={9.5}>NOT LEGAL ADVICE · VERIFY WITH HRM PLANNING</Mono>
    </footer>
  );
};

Object.assign(window, { AUTH_AREA, AccountMenu, AuthBar, AuthFooter, ThemeSwitch });
