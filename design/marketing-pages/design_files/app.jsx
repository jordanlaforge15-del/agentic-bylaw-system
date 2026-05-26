// Root app — routing, theme + tweaks state.

const { useState } = React;

const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "homeVariant": "safe",
  "defaultMode": "light"
}/*EDITMODE-END*/;

const App = () => {
  const [route, setRoute] = useState('home');
  const [mode, setMode] = useState(TWEAK_DEFAULTS.defaultMode);
  const [tweaks, setTweak] = useTweaks(TWEAK_DEFAULTS);

  // hash routing
  const EXTRA_ROUTES = ['app', 'changelog', 'coverage', 'support', 'about', 'privacy', 'terms'];
  React.useEffect(() => {
    const sync = () => {
      const h = (window.location.hash || '#home').slice(1);
      if (NAV.find(n => n.id === h) || EXTRA_ROUTES.includes(h)) setRoute(h);
    };
    sync();
    window.addEventListener('hashchange', sync);
    return () => window.removeEventListener('hashchange', sync);
  }, []);

  const go = (r) => {
    setRoute(r);
    window.location.hash = r;
    window.scrollTo({ top: 0, behavior: 'instant' });
  };

  // The chat app is a full-bleed product surface — no top nav / footer.
  if (route === 'app') {
    return (
      <ThemeProvider mode={mode}>
        <AppPage setRoute={go} mode={mode} setMode={setMode} />
      </ThemeProvider>
    );
  }

  const PageBody = () => {
    switch (route) {
      case 'pricing': return <PricingPage setRoute={go} />;
      case 'login': return <LoginPage setRoute={go} />;
      case 'signup': return <SignupPage setRoute={go} />;
      case 'billing': return <BillingPage setRoute={go} />;
      case 'changelog': return <ChangelogPage setRoute={go} />;
      case 'coverage': return <CoveragePage setRoute={go} />;
      case 'support': return <SupportPage setRoute={go} />;
      case 'about': return <AboutPage setRoute={go} />;
      case 'privacy': return <PrivacyPage setRoute={go} />;
      case 'terms': return <TermsPage setRoute={go} />;
      default: return <HomePage setRoute={go} variant={tweaks.homeVariant} />;
    }
  };

  return (
    <ThemeProvider mode={mode}>
      <PageShell>
        <TopNav route={route} setRoute={go} mode={mode} setMode={setMode} />
        <PageBody />
        <Footer setRoute={go} />
      </PageShell>

      <TweaksPanel title="ABS Site · Tweaks">
        <TweakSection title="Home page">
          <TweakRadio
            label="Variant"
            value={tweaks.homeVariant}
            onChange={(v) => setTweak('homeVariant', v)}
            options={[
              { value: 'safe', label: 'Safe' },
              { value: 'bold', label: 'Bold' },
            ]}
          />
        </TweakSection>
        <TweakSection title="Default mode">
          <TweakRadio
            label="Loads in"
            value={tweaks.defaultMode}
            onChange={(v) => { setTweak('defaultMode', v); setMode(v); }}
            options={[
              { value: 'light', label: 'Blueprint' },
              { value: 'dark', label: 'Setback' },
            ]}
          />
        </TweakSection>
      </TweaksPanel>
    </ThemeProvider>
  );
};

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
