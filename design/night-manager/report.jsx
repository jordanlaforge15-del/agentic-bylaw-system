// REPORT VIEWER — renders the morning-after markdown report as rich HTML.

function ReportViewer() {
  const reports = [
    { id: "current", date: "May 23, 2026 · 23:00 (in progress)", merged: "2", failed: "1", blocked: "0", duration: "ongoing", current: true },
    ...NM_REPORTS,
  ];
  const [active, setActive] = useState(reports[1].id); // first completed report

  const r = reports.find((x) => x.id === active);

  return (
    <div className="report">
      <Panel id="ARCH" title="REPORT ARCHIVE" right={<span>{reports.length}</span>} flush>
        <div className="report-list">
          {reports.map((rep) => (
            <div
              key={rep.id}
              className={`report-list__item ${active === rep.id ? "report-list__item--active" : ""}`}
              onClick={() => setActive(rep.id)}
            >
              <div className="report-list__date">{rep.date}</div>
              <div className="report-list__sum">
                <span className="ok"><b>{rep.merged}</b> merged</span>
                <span className="err"><b>{rep.failed}</b> failed</span>
                {rep.blocked !== "0" && rep.blocked > 0 && <span className="warn"><b>{rep.blocked}</b> blocked</span>}
                <span className="mute right">{rep.duration}</span>
              </div>
            </div>
          ))}
        </div>
      </Panel>

      <Panel id="RPT" title={`REPORT · ${r.id}`} right={<span>.night-manager/{r.id}.md</span>} flush>
        <ReportDoc rep={r} />
      </Panel>
    </div>
  );
}

function ReportDoc({ rep }) {
  // We render fully for the May 22 report; others get a thinner skeleton.
  if (rep.id === "current") {
    return (
      <div className="report-doc">
        <h1>Night Manager Run · In Progress</h1>
        <p className="lede">
          Run <span className="prim">nm-20260523-2300</span> · started 23:00 ·{" "}
          <span className="info">3 agents active</span> · final report will be generated on completion.
        </p>
        <div className="stats-strip">
          <div className="cell"><div className="lbl">Merged</div><div className="val ok">2</div></div>
          <div className="cell"><div className="lbl">Failed</div><div className="val err">1</div></div>
          <div className="cell"><div className="lbl">In flight</div><div className="val">4</div></div>
          <div className="cell"><div className="lbl">Queued</div><div className="val">2</div></div>
        </div>
      </div>
    );
  }

  return (
    <div className="report-doc">
      <h1>Night Manager Run · {rep.date}</h1>
      <p className="lede">
        Run <span className="prim">nm-20260522-2300</span> processed{" "}
        <span className="ok">{rep.merged} merged</span>,{" "}
        <span className="err">{rep.failed} failed</span>, and{" "}
        <span className="warn">{rep.blocked} blocked</span> issues over{" "}
        <span className="prim">{rep.duration}</span>. Deploy gate triggered at 04:14:31.
      </p>

      <div className="stats-strip">
        <div className="cell"><div className="lbl">Merged</div><div className="val ok">{rep.merged}</div></div>
        <div className="cell"><div className="lbl">Failed</div><div className="val err">{rep.failed}</div></div>
        <div className="cell"><div className="lbl">Blocked</div><div className="val warn">{rep.blocked}</div></div>
        <div className="cell"><div className="lbl">Duration</div><div className="val">{rep.duration}</div></div>
      </div>

      <h2>Merged</h2>
      <ul>
        {[
          ["ABS-82", "Add Slack handler for billing.failed events", "21m 14s · 1 review pass"],
          ["ABS-83", "Refactor /api/users response shape", "33m 02s · 1 review pass"],
          ["ABS-84", "Bug: Onboarding tour skips org step on resize", "12m 58s · 1 review pass"],
          ["ABS-85", "Cache invalidation on org rename", "44m 27s · 2 review passes"],
          ["ABS-86", "Linear webhook idempotency key", "18m 04s · 1 review pass"],
          ["ABS-87", "Dark mode tokens for marketing site", "27m 41s · 1 review pass"],
          ["ABS-88", "Stripe portal session URL expires too fast", "9m 52s · 1 review pass"],
          ["ABS-89", "Move legacy /v1 routes behind deprecation header", "1h 02m · 3 review passes"],
        ].map(([id, txt, meta]) => (
          <li key={id}>
            <span className="id">{id}</span>
            <span className="txt">{txt}</span>
            <span className="meta">{meta}</span>
          </li>
        ))}
      </ul>

      <h2>Failed</h2>
      <ul>
        <li>
          <span className="id err">ABS-81</span>
          <span className="txt">Migrate audit log table to partitioned schema — alembic migration timed out at 35min on staging-like dataset; reverted.</span>
          <span className="meta">2 attempts · marked failed</span>
        </li>
      </ul>

      <h2>Deploy</h2>
      <ul>
        <li><span className="id">▲</span><span className="txt">dev → staging promoted at 04:14:31</span><span className="meta">8 commits</span></li>
        <li><span className="id">▲</span><span className="txt">staging → production promoted at 04:23:08</span><span className="meta">smoke OK</span></li>
      </ul>

      <h2>Operator notes</h2>
      <p style={{ color: "var(--text-dim)", lineHeight: 1.7 }}>
        Review attempts averaged 1.3 per issue, down from 1.8 last week. The single failure (ABS-81) is a known
        infrastructure constraint — recommend running migrations off the NM pipeline. Total agent-hours: 8h 47m
        across 9 issues, parallel throughput 1.69×.
      </p>
    </div>
  );
}

Object.assign(window, { ReportViewer });
