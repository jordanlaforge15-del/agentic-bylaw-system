// Shared primitives and hooks for Night Manager UI.
const { useState, useEffect, useRef, useMemo, useCallback } = React;

// ─────────── Clock — ticks every second
function useTick(intervalMs = 1000) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}

// Pretend "current time" — anchored to a fake mid-run timestamp + a real clock offset
// so elapsed durations advance naturally. Run started at 23:00:00 local. We're at ~02:47:32 currently.
const RUN_EPOCH = new Date("2026-05-23T23:00:00").getTime();
const NOW_EPOCH = new Date("2026-05-24T02:47:32").getTime();
function useFakeNow() {
  const realStart = useRef(Date.now());
  const tick = useTick(1000);
  return NOW_EPOCH + (tick - realStart.current);
}

function fmtElapsed(ms) {
  if (ms == null || ms < 0) ms = 0;
  const s = Math.floor(ms / 1000);
  const hh = Math.floor(s / 3600);
  const mm = Math.floor((s % 3600) / 60);
  const ss = s % 60;
  const pad = (n) => String(n).padStart(2, "0");
  return `${pad(hh)}:${pad(mm)}:${pad(ss)}`;
}

function fmtMinutes(ms) {
  if (ms == null || ms < 0) return "—";
  const s = Math.floor(ms / 1000);
  const mm = Math.floor(s / 60);
  const ss = s % 60;
  return `${mm}m ${String(ss).padStart(2, "0")}s`;
}

function fmtClock(ms) {
  const d = new Date(ms);
  const pad = (n) => String(n).padStart(2, "0");
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function parseISO(s) {
  return s ? new Date(s).getTime() : null;
}

// ─────────── Status helpers
const STATUS_LABEL = {
  queued: "QUEUED",
  in_progress: "RUNNING",
  reviewing: "REVIEW",
  merged: "MERGED",
  failed: "FAILED",
  blocked: "BLOCKED",
};
const STATUS_TONE = {
  queued: "queued",
  in_progress: "info",
  reviewing: "review",
  merged: "ok",
  failed: "err",
  blocked: "warn",
};

function StatusPill({ status, solid }) {
  const tone = STATUS_TONE[status] || "queued";
  return (
    <span className={`pill pill--${tone} ${solid ? "pill--solid" : ""}`}>
      {status === "in_progress" && <span className="ring" style={{ width: 8, height: 8 }} />}
      {status !== "in_progress" && <span className={`dot dot--${tone} ${status === "in_progress" ? "dot--pulse" : ""}`} />}
      {STATUS_LABEL[status] || status?.toUpperCase()}
    </span>
  );
}

function Dot({ tone = "queued", pulse }) {
  return <span className={`dot dot--${tone} ${pulse ? "dot--pulse" : ""}`} />;
}

// ─────────── Panel
function Panel({ title, id, meta, right, children, flush, variant, className = "", style }) {
  const cls = `panel ${variant ? `panel--${variant}` : ""} ${className}`;
  return (
    <div className={cls} style={style}>
      <span className="tick-bl" />
      <span className="tick-br" />
      {(title || id || meta || right) && (
        <div className="panel-h">
          {id && <span className="id">{id}</span>}
          {title && <span className="title">{title}</span>}
          {meta && <span style={{ color: "var(--text-mute)" }}>{meta}</span>}
          {right && <span className="meta">{right}</span>}
        </div>
      )}
      <div className={`panel-b ${flush ? "panel-b--flush" : ""}`}>{children}</div>
    </div>
  );
}

// KPI block
function KPI({ label, value, unit, tone, hint }) {
  return (
    <div className={`kpi ${tone ? `kpi--${tone}` : ""}`}>
      <div className="lbl">{label}</div>
      <div className="val">
        {value}
        {unit && <span className="unit">{unit}</span>}
      </div>
      {hint && <div className="up" style={{ marginTop: 4 }}>{hint}</div>}
    </div>
  );
}

// Logo mark — concentric brackets, evokes nightfall / a target reticle
function NightManagerMark({ size = 16 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="none">
      <circle cx="8" cy="8" r="6.5" stroke="currentColor" strokeWidth="1" />
      <path d="M8 1.5 V 4 M8 12 V 14.5 M1.5 8 H 4 M12 8 H 14.5" stroke="currentColor" strokeWidth="1" />
      <circle cx="8" cy="8" r="2" fill="currentColor" />
    </svg>
  );
}

// ─────────── Sparkline
function Sparkline({ values, width = 80, height = 20, color = "var(--primary)" }) {
  if (!values || values.length === 0) return null;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const stepX = width / (values.length - 1 || 1);
  const pts = values.map((v, i) => `${i * stepX},${height - ((v - min) / span) * height}`).join(" ");
  return (
    <svg width={width} height={height} style={{ display: "block" }}>
      <polyline points={pts} fill="none" stroke={color} strokeWidth="1" opacity="0.9" />
    </svg>
  );
}

// ─────────── Live log "feel" — returns the first N events from a stream,
// advancing N over time so logs appear to be streaming in.
function useStreamingLog(events, intervalMs = 1800, startCount = null) {
  const initial = startCount ?? events.length;
  const [count, setCount] = useState(initial);
  useEffect(() => {
    if (count >= events.length) return;
    const id = setTimeout(() => setCount((c) => Math.min(c + 1, events.length)), intervalMs);
    return () => clearTimeout(id);
  }, [count, events.length, intervalMs]);
  return events.slice(0, count);
}

// ─────────── Custom field controls
function Field({ label, hint, children }) {
  return (
    <div className="form-row">
      <div className="lbl-grp">
        <span className="lbl">{label}</span>
        {hint && <span className="hint">{hint}</span>}
      </div>
      <div>{children}</div>
    </div>
  );
}

function Segmented({ options, value, onChange }) {
  return (
    <div className="seg">
      {options.map((opt) => {
        const v = typeof opt === "string" ? opt : opt.value;
        const lbl = typeof opt === "string" ? opt : opt.label;
        return (
          <button
            key={v}
            className={`seg__opt ${value === v ? "seg__opt--active" : ""}`}
            onClick={() => onChange(v)}
          >
            {lbl}
          </button>
        );
      })}
    </div>
  );
}

function Toggle({ value, onChange, label }) {
  return (
    <button className={`toggle ${value ? "toggle--on" : ""}`} onClick={() => onChange(!value)}>
      <span className="toggle__sw" />
      {label && <span className="toggle__lbl">{label}</span>}
    </button>
  );
}

// ─────────── Crosshair / data grid icon — for plan group icons
function GroupGlyph({ n }) {
  return (
    <span style={{
      display: "inline-flex",
      alignItems: "center",
      justifyContent: "center",
      width: 20, height: 20,
      border: "1px solid var(--line-2)",
      color: "var(--primary)",
      fontSize: 10,
      letterSpacing: "0.05em",
      fontVariantNumeric: "tabular-nums",
    }}>
      {String(n).padStart(2, "0")}
    </span>
  );
}

Object.assign(window, {
  useTick, useFakeNow, fmtElapsed, fmtMinutes, fmtClock, parseISO,
  STATUS_LABEL, STATUS_TONE,
  StatusPill, Dot, Panel, KPI, NightManagerMark,
  Sparkline, useStreamingLog,
  Field, Segmented, Toggle, GroupGlyph,
  RUN_EPOCH, NOW_EPOCH,
});
