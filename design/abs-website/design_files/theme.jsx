// Theme tokens + provider. Mirrors the system spec from the foundations.

const ABSTheme = {
  light: {
    name: 'Blueprint',
    surface: '#ffffff',
    surfaceAlt: '#f7f6f2',
    surfaceInk: '#0a0a0a',
    text: '#0a0a0a',
    textMuted: '#7a7468',
    rule: 'rgba(10,10,10,0.45)',
    hair: 'rgba(10,10,10,0.1)',
    accent: '#c9f24c',
    accentInk: '#5a7a1a',
    onAccent: '#0a0a0a',
    brick: '#a64b2a',
    overlay: 'rgba(10,10,10,0.5)',
  },
  dark: {
    name: 'Setback',
    surface: '#0a0a0a',
    surfaceAlt: '#171614',
    surfaceInk: '#ede8db',
    text: '#ede8db',
    textMuted: '#9a9484',
    rule: 'rgba(237,232,219,0.5)',
    hair: 'rgba(237,232,219,0.12)',
    accent: '#c9f24c',
    accentInk: '#c9f24c',
    onAccent: '#0a0a0a',
    brick: '#d56a44',
    overlay: 'rgba(237,232,219,0.05)',
  },
};

const ThemeCtx = React.createContext(ABSTheme.light);
const useTheme = () => React.useContext(ThemeCtx);

const ThemeProvider = ({ mode, children }) => {
  React.useEffect(() => { document.body.setAttribute('data-mode', mode); }, [mode]);
  return <ThemeCtx.Provider value={ABSTheme[mode]}>{children}</ThemeCtx.Provider>;
};

// — Atomic UI primitives that read theme

const ABSLogo = ({ size = 24 }) => {
  const t = useTheme();
  return (
    <div style={{
      fontFamily: 'Inter Tight, sans-serif', fontWeight: 800,
      fontSize: size, color: t.text, letterSpacing: '-0.06em',
      lineHeight: 1, display: 'inline-flex', alignItems: 'center',
    }}>
      <span>abs</span>
      <span style={{ display: 'inline-block', width: size * 0.18, height: size * 0.78, background: t.accent, marginLeft: size * 0.08 }} />
    </div>
  );
};

const Btn = ({ children, variant = 'primary', size = 'md', ...rest }) => {
  const t = useTheme();
  const sizes = {
    sm: { padding: '8px 12px', fontSize: 12.5 },
    md: { padding: '11px 18px', fontSize: 13.5 },
    lg: { padding: '14px 22px', fontSize: 14.5 },
  };
  const variants = {
    primary: { background: t.text, color: t.surface, border: '1.5px solid ' + t.text },
    accent: { background: t.accent, color: t.onAccent, border: '1.5px solid ' + t.accent },
    ghost: { background: 'transparent', color: t.text, border: '1.5px solid ' + t.text },
    quiet: { background: 'transparent', color: t.textMuted, border: '1.5px solid ' + t.hair },
  };
  return (
    <button {...rest} style={{
      ...variants[variant], ...sizes[size],
      fontFamily: 'inherit', fontWeight: 600, letterSpacing: '-0.01em',
      cursor: 'pointer', transition: 'transform 0.08s, opacity 0.12s',
      ...rest.style,
    }}
    onMouseDown={e => e.currentTarget.style.transform = 'translateY(1px)'}
    onMouseUp={e => e.currentTarget.style.transform = ''}
    onMouseLeave={e => e.currentTarget.style.transform = ''}
    >{children}</button>
  );
};

const Mono = ({ children, muted, accent, size = 10, ...rest }) => {
  const t = useTheme();
  return <span {...rest} style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: size, letterSpacing: '0.14em', textTransform: 'uppercase', color: accent ? t.accentInk : muted ? t.textMuted : t.text, ...rest.style }}>{children}</span>;
};

const HighlightWord = ({ children, height = 0.18 }) => {
  const t = useTheme();
  return (
    <span style={{ position: 'relative', display: 'inline-block' }}>
      {children}
      <span style={{ position: 'absolute', left: 0, right: 0, bottom: '14%', height: `${height}em`, background: t.accent, zIndex: -1 }} />
    </span>
  );
};

Object.assign(window, { ABSTheme, ThemeCtx, useTheme, ThemeProvider, ABSLogo, Btn, Mono, HighlightWord });
