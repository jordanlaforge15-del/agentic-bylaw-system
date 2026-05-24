"use client";

const THEMES = [
  { value: "apollo", label: "APOLLO", swatch: "#ffb000" },
  { value: "vanguard", label: "VANGUARD", swatch: "#5fe1d3" },
  { value: "red_october", label: "RED OCTOBER", swatch: "#ff0033" },
] as const;

export function ThemeSwitch({
  theme,
  onTheme,
}: {
  theme: string;
  onTheme: (v: string) => void;
}) {
  return (
    <div className="nm-theme-switch">
      <span className="nm-theme-switch__lbl">DISPLAY MODE</span>
      <div
        className="nm-theme-switch__group"
        role="radiogroup"
        aria-label="Theme"
      >
        {THEMES.map((t) => {
          const active = theme === t.value;
          return (
            <button
              key={t.value}
              className={`nm-theme-switch__opt ${active ? "nm-theme-switch__opt--active" : ""}`}
              onClick={() => onTheme(t.value)}
              style={{ color: t.swatch }}
              title={t.label}
              aria-pressed={active}
            >
              <span
                className="nm-theme-switch__swatch"
                style={{ "--sw": t.swatch } as React.CSSProperties}
              />
              <span className="nm-theme-switch__name">{t.label}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
