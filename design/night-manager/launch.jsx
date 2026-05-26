// LAUNCH — start a new Night Manager run.

function Launch({ onStart }) {
  const [maxAgents, setMaxAgents] = useState(3);
  const [label, setLabel] = useState("Triaged");
  const [model, setModel] = useState("opus");
  const [deploy, setDeploy] = useState(true);
  const [dryRun, setDryRun] = useState(false);
  const [override, setOverride] = useState("");

  const selected = override ? NM_TRIAGED.filter(i => i.id === override) : NM_TRIAGED;
  const groups = useMemo(() => {
    const out = [];
    for (let i = 0; i < selected.length; i += maxAgents) {
      out.push(selected.slice(i, i + maxAgents));
    }
    return out;
  }, [selected, maxAgents]);

  return (
    <div className="launch">
      <div className="col" style={{ gap: 12, minWidth: 0 }}>
        <Panel id="CFG-01" title="LAUNCH CONFIGURATION" right={<span>./scripts/start-night-manager.sh</span>} flush>
          <Field label="Max parallel agents" hint="how many Claude Code processes can run concurrently (default 3)">
            <div className="row" style={{ gap: 14 }}>
              <input
                className="input"
                type="number"
                min={1} max={10}
                value={maxAgents}
                onChange={(e) => setMaxAgents(Math.max(1, Math.min(10, +e.target.value || 1)))}
                style={{ width: 80 }}
              />
              <div className="row" style={{ gap: 4 }}>
                {[1,2,3,4,5,6,7,8].map(n => (
                  <span key={n} style={{
                    width: 14, height: 22,
                    border: "1px solid var(--line-2)",
                    background: n <= maxAgents ? "var(--primary)" : "transparent",
                    boxShadow: n <= maxAgents ? "var(--glow-soft)" : "none",
                  }} />
                ))}
              </div>
              <span className="mute" style={{ marginLeft: 8 }}>≈ {Math.ceil(NM_TRIAGED.length / maxAgents)} groups</span>
            </div>
          </Field>

          <Field label="Label filter" hint="Linear label used to pull the issue queue">
            <input className="input" value={label} onChange={(e) => setLabel(e.target.value)} style={{ maxWidth: 280 }} />
          </Field>

          <Field label="Model" hint="default opus · sonnet for speed, haiku for cheap experiments">
            <Segmented
              value={model}
              onChange={setModel}
              options={[
                { value: "opus", label: "OPUS" },
                { value: "sonnet", label: "SONNET" },
                { value: "haiku", label: "HAIKU" },
              ]}
            />
          </Field>

          <Field label="Deploy after merge" hint="promote dev → staging → prod once all groups complete">
            <Toggle value={deploy} onChange={setDeploy} label={deploy ? "ENABLED" : "DISABLED"} />
          </Field>

          <Field label="Single-issue override" hint="optional · pulls only this issue and ignores label filter">
            <input className="input" value={override} onChange={(e) => setOverride(e.target.value.toUpperCase())} placeholder="e.g. ABS-101" style={{ maxWidth: 200 }} />
          </Field>

          <Field label="Dry run" hint="plan + preview only — no agents spawned, no merges">
            <Toggle value={dryRun} onChange={setDryRun} label={dryRun ? "ON" : "OFF"} />
          </Field>
        </Panel>

        <Panel id="CMD-02" title="EFFECTIVE COMMAND" right={<span>copy</span>}>
          <div style={{ fontFamily: "var(--font-mono)", fontSize: 13, color: "var(--text)", padding: "4px 0", wordBreak: "break-word" }}>
            <span className="mute">$</span>{" "}
            <span className="prim">./scripts/start-night-manager.sh</span>{" "}
            <span>--max-agents {maxAgents}</span>{" "}
            <span>--label "{label}"</span>{" "}
            <span>--model {model}</span>{" "}
            {deploy && <span>--deploy </span>}
            {override && <span>--issue {override} </span>}
            {dryRun && <span>--dry-run</span>}
          </div>
        </Panel>
      </div>

      <div className="col" style={{ gap: 12, minWidth: 0 }}>
        <Panel
          id="QUE-03"
          title="LINEAR QUEUE PREVIEW"
          right={
            <span>
              <span className="dot dot--ok" /> {selected.length} matching · label "{label}"
            </span>
          }
          flush
        >
          <div style={{ maxHeight: 380, overflowY: "auto" }}>
            <table className="tbl">
              <thead>
                <tr>
                  <th style={{ width: 90 }}>ID</th>
                  <th>Title</th>
                  <th style={{ width: 60, textAlign: "right" }}>Est</th>
                </tr>
              </thead>
              <tbody>
                {selected.map((i) => (
                  <tr key={i.id}>
                    <td className="id">{i.id}</td>
                    <td className="truncate" style={{ maxWidth: 280 }}>{i.title}</td>
                    <td style={{ textAlign: "right", color: "var(--text-mute)" }}>{i.est}</td>
                  </tr>
                ))}
                {selected.length === 0 && (
                  <tr><td colSpan="3" style={{ textAlign: "center", color: "var(--text-mute)", padding: 20 }}>no matches</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </Panel>

        <Panel id="PLN-04" title="PLANNED EXECUTION" flush>
          <div style={{ padding: 12, display: "flex", flexDirection: "column", gap: 8 }}>
            {groups.map((g, i) => (
              <div key={i} style={{
                border: "1px solid var(--line-2)",
                padding: "8px 10px",
                display: "grid",
                gridTemplateColumns: "70px 1fr",
                gap: 12,
                alignItems: "center",
                background: "var(--bg-2)",
              }}>
                <div>
                  <div className="up" style={{ color: "var(--text-mute)" }}>group</div>
                  <div className="prim" style={{ fontSize: 18, letterSpacing: "0.04em" }}>G{String(i + 1).padStart(2, "0")}</div>
                </div>
                <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
                  {g.map((it) => (
                    <span key={it.id} className="pill" style={{ color: "var(--text-dim)" }}>{it.id}</span>
                  ))}
                </div>
              </div>
            ))}
            {deploy && (
              <div style={{
                border: "1px dashed var(--line-2)",
                padding: "10px 12px",
                color: "var(--text-mute)",
                textAlign: "center",
                letterSpacing: "0.12em",
                textTransform: "uppercase",
                fontSize: 11,
              }}>
                ▲ then DEPLOY · dev → staging → prod
              </div>
            )}
          </div>
        </Panel>

        <Panel id="LCH-05" title="" flush>
          <div style={{ padding: 16, display: "flex", gap: 12, alignItems: "center", borderTop: "1px solid var(--line)" }}>
            <div className="grow">
              <div className="up" style={{ color: "var(--text-mute)" }}>ready to launch</div>
              <div className="mono" style={{ fontSize: 12, color: "var(--text-dim)", marginTop: 2 }}>
                {selected.length} issues · {Math.ceil(selected.length / maxAgents)} groups · {model} · {deploy ? "deploy on" : "no deploy"}
              </div>
            </div>
            <button className="btn btn--ghost">CANCEL</button>
            <button className="btn btn--prim" onClick={() => onStart && onStart()}>
              ▶ INITIATE RUN
            </button>
          </div>
        </Panel>
      </div>
    </div>
  );
}

Object.assign(window, { Launch });
