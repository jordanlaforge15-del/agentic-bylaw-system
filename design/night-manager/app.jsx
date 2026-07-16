// APP SHELL — top bar, nav, screen routing, theme tweaks.

function App() {
  const [screen, setScreen] = useState("dashboard"); // dashboard | issue | launch | report
  const [openIssue, setOpenIssue] = useState(null);
  const fakeNow = useFakeNow();

  const [t, setTweak] = useTweaks(TWEAK_DEFAULTS);

  // Apply theme to body. WebKit caches resolved CSS-variable values on existing
  // elements when an ancestor's data-attribute changes, so we force a reflow
  // by briefly toggling display:none. Without this, plan-slot border colors
  // (which use var(--info), var(--ok), etc.) stay stuck on the prior theme.
  useEffect(() => {
    document.body.setAttribute("data-theme", t.theme);
    document.body.style.display = "none";
    // eslint-disable-next-line no-unused-expressions
    document.body.offsetHeight;
    document.body.style.display = "";
  }, [t.theme]);

  const elapsedMs = fakeNow - RUN_EPOCH;
  const total = Object.keys(NM_DATA.issues).length;
  const merged = Object.values(NM_DATA.issues).filter((i) => i.status === "merged").length;
  const failed = Object.values(NM_DATA.issues).filter((i) => i.status === "failed").length;
  const active = Object.values(NM_DATA.issues).filter((i) => i.status === "in_progress" || i.status === "reviewing").length;

  return (
    <div className="app">
      <TopBar fakeNow={fakeNow} elapsedMs={elapsedMs} active={active} theme={t.theme} onTheme={(v) => setTweak("theme", v)} />
      <NavBar
        screen={screen}
        onScreen={setScreen}
        merged={merged}
        total={total}
        failed={failed}
        active={active}
      />

      <div className="console">
        {screen === "dashboard" && (
          <Dashboard
            fakeNow={fakeNow}
            onOpenIssue={(id) => { setOpenIssue(id); setScreen("issue"); }}
          />
        )}
        {screen === "issue" && (
          <IssueDetail
            identifier={openIssue || "ABS-95"}
            onBack={() => setScreen("dashboard")}
            fakeNow={fakeNow}
          />
        )}
        {screen === "launch" && <Launch onStart={() => setScreen("dashboard")} />}
        {screen === "report" && <ReportViewer />}
      </div>

      <FootStrip fakeNow={fakeNow} />

      <TweaksPanel title="TWEAKS · DISPLAY">
        <TweakSection label="THEME">
          <TweakRadio
            label="Aesthetic"
            value={t.theme}
            options={[
              { value: "apollo",      label: "Apollo" },
              { value: "vanguard",    label: "Vanguard" },
              { value: "red_october", label: "Red Oct." },
            ]}
            onChange={(v) => setTweak("theme", v)}
          />
          <div style={{ fontSize: 11, color: "#888", lineHeight: 1.55, padding: "8px 0" }}>
            <div style={{ marginBottom: 4 }}><b>Apollo</b> — 1970s NASA console, amber on warm black.</div>
            <div style={{ marginBottom: 4 }}><b>Vanguard</b> — modern flight deck, cyan on graphite.</div>
            <div><b>Red October</b> — submarine command bridge, alarm red on pure black.</div>
          </div>
        </TweakSection>
        <TweakSection label="JUMP TO">
          <TweakButton label="Dashboard"        onClick={() => setScreen("dashboard")} />
          <TweakButton label="Issue · running"  onClick={() => { setOpenIssue("ABS-95"); setScreen("issue"); }} />
          <TweakButton label="Issue · failed"   onClick={() => { setOpenIssue("ABS-92"); setScreen("issue"); }} />
          <TweakButton label="Issue · merged"   onClick={() => { setOpenIssue("ABS-90"); setScreen("issue"); }} />
          <TweakButton label="Launch"           onClick={() => setScreen("launch")} />
          <TweakButton label="Report viewer"    onClick={() => setScreen("report")} />
        </TweakSection>
      </TweaksPanel>
    </div>
  );
}

// ─────────── TOP BAR
function TopBar({ fakeNow, elapsedMs, active, theme, onTheme }) {
  return (
    <div className="topbar">
      <div className="topbar__brand">
        <div className="topbar__logo"><NightManagerMark size={16} /></div>
        <div>
          <div className="topbar__title">NIGHT MANAGER</div>
          <div className="topbar__sub">mission console · v3.2.0</div>
        </div>
      </div>

      <div className="topbar__center">
        <div className="topbar__stat">
          <span className="dot dot--ok dot--pulse" />
          <span>SYSTEM</span>
          <b style={{ color: "var(--ok)" }}>NOMINAL</b>
        </div>
        <div className="topbar__stat">
          <span>RUN</span>
          <b className="prim">{NM_DATA.run_id}</b>
        </div>
        <div className="topbar__stat">
          <span>STARTED</span>
          <b>23:00:00</b>
        </div>
        <div className="topbar__stat">
          <span>ELAPSED</span>
          <span className="display">{fmtElapsed(elapsedMs)}</span>
        </div>
        <div className="topbar__stat">
          <span>AGENTS</span>
          <span className="display" style={{ fontSize: 16 }}>{active} / {NM_DATA.config.max_agents}</span>
        </div>
        <div className="topbar__stat right">
          <span>OPERATOR</span>
          <b>@nm-ops</b>
        </div>
      </div>

      <ThemeSwitch theme={theme} onTheme={onTheme} />

      <div className="topbar__right">
        <div className="topbar__clock">
          <span className="lbl">LOCAL TIME</span>
          <span className="display">{fmtClock(fakeNow)}</span>
        </div>
        <div className="topbar__clock">
          <span className="lbl">UTC</span>
          <span className="display">{fmtClock(fakeNow + 7 * 3600 * 1000)}</span>
        </div>
      </div>
    </div>
  );
}

// ─────────── THEME SWITCH
const THEMES = [
  { value: "apollo",      label: "APOLLO",      swatch: "#ffb000" },
  { value: "vanguard",    label: "VANGUARD",    swatch: "#5fe1d3" },
  { value: "red_october", label: "RED OCTOBER", swatch: "#ff0033" },
];
function ThemeSwitch({ theme, onTheme }) {
  return (
    <div className="theme-switch">
      <span className="theme-switch__lbl">DISPLAY MODE</span>
      <div className="theme-switch__group" role="radiogroup" aria-label="Theme">
        {THEMES.map((t) => {
          const active = theme === t.value;
          return (
            <button
              key={t.value}
              className={`theme-switch__opt ${active ? "theme-switch__opt--active" : ""}`}
              onClick={() => onTheme(t.value)}
              style={{ color: t.swatch }}
              title={t.label}
              aria-pressed={active}
            >
              <span className="theme-switch__swatch" style={{ "--sw": t.swatch }} />
              <span className="theme-switch__name">{t.label}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

// ─────────── NAV BAR
function NavBar({ screen, onScreen, merged, total, failed, active }) {
  return (
    <div className="nav">
      <button
        className={`nav__item ${screen === "dashboard" ? "nav__item--active" : ""}`}
        onClick={() => onScreen("dashboard")}
      >
        <span className="num">01</span> DASHBOARD
      </button>
      <button
        className={`nav__item ${screen === "issue" ? "nav__item--active" : ""}`}
        onClick={() => onScreen("issue")}
      >
        <span className="num">02</span> ISSUE DETAIL
      </button>
      <button
        className={`nav__item ${screen === "launch" ? "nav__item--active" : ""}`}
        onClick={() => onScreen("launch")}
      >
        <span className="num">03</span> LAUNCH
      </button>
      <button
        className={`nav__item ${screen === "report" ? "nav__item--active" : ""}`}
        onClick={() => onScreen("report")}
      >
        <span className="num">04</span> REPORTS
      </button>
      <div className="nav__spacer" />
      <div className="nav__right">
        <div className="kv"><span className="dot dot--info dot--pulse" /> <span>{active} agents live</span></div>
        <div className="kv"><span className="dot dot--ok" /> <span>{merged}/{total} merged</span></div>
        {failed > 0 && <div className="kv"><span className="dot dot--err" /> <span>{failed} failed</span></div>}
      </div>
    </div>
  );
}

// ─────────── FOOTER STATUS STRIP — flight-deck-style stamps
function FootStrip({ fakeNow }) {
  return (
    <div style={{
      display: "flex",
      alignItems: "stretch",
      borderTop: "1px solid var(--line)",
      background: "var(--bg-1)",
      fontSize: 10,
      letterSpacing: "0.12em",
      textTransform: "uppercase",
      color: "var(--text-mute)",
      padding: "0 18px",
      height: 26,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, paddingRight: 18, borderRight: "1px solid var(--line)" }}>
        <span className="dot dot--ok" />
        <span>STATE.JSON · OK</span>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "0 18px", borderRight: "1px solid var(--line)" }}>
        <span className="dot dot--ok" />
        <span>LINEAR · 18ms</span>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "0 18px", borderRight: "1px solid var(--line)" }}>
        <span className="dot dot--ok" />
        <span>CLAUDE API · 240ms</span>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "0 18px", borderRight: "1px solid var(--line)" }}>
        <span className="dot dot--warn" />
        <span>DISK · 62%</span>
      </div>
      <div style={{ flex: 1 }} />
      <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "0 18px", borderLeft: "1px solid var(--line)" }}>
        <span>BUILD · 2026.05.22</span>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "0 18px", borderLeft: "1px solid var(--line)" }}>
        <span className="cursor">SYNCED {fmtClock(fakeNow)}</span>
      </div>
    </div>
  );
}

// ─────────── TWEAKS DEFAULTS
const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "theme": "vanguard"
}/*EDITMODE-END*/;

// Mount
const root = ReactDOM.createRoot(document.getElementById("root"));
root.render(<App />);
