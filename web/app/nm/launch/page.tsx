"use client";

import { useState, useMemo } from "react";
import { useRouter } from "next/navigation";
import { Panel } from "../components/panel";

export default function LaunchPage() {
  const router = useRouter();
  const [maxAgents, setMaxAgents] = useState(3);
  const [label, setLabel] = useState("Triaged");
  const [model, setModel] = useState("opus");
  const [deploy, setDeploy] = useState(true);
  const [dryRun, setDryRun] = useState(false);
  const [override, setOverride] = useState("");
  const [launching, setLaunching] = useState(false);

  const groups = useMemo(() => {
    const count = override ? 1 : 9;
    return Math.ceil(count / maxAgents);
  }, [maxAgents, override]);

  async function handleLaunch() {
    setLaunching(true);
    try {
      await fetch("/api/nm/run/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          maxAgents,
          label,
          model,
          deploy,
          issue: override || undefined,
          dryRun,
        }),
      });
      router.push("/nm");
    } catch {
      setLaunching(false);
    }
  }

  return (
    <div className="nm-launch">
      <div className="nm-col" style={{ gap: 12, minWidth: 0 }}>
        <Panel
          id="CFG-01"
          title="LAUNCH CONFIGURATION"
          right={<span>./scripts/start-night-manager.sh</span>}
          flush
        >
          <FormRow
            label="Max parallel agents"
            hint="how many Claude Code processes can run concurrently (default 3)"
          >
            <div className="nm-row" style={{ gap: 14 }}>
              <input
                className="nm-input"
                type="number"
                min={1}
                max={10}
                value={maxAgents}
                onChange={(e) =>
                  setMaxAgents(
                    Math.max(1, Math.min(10, +e.target.value || 1)),
                  )
                }
                style={{ width: 80 }}
              />
              <div className="nm-row" style={{ gap: 4 }}>
                {[1, 2, 3, 4, 5, 6, 7, 8].map((n) => (
                  <span
                    key={n}
                    style={{
                      width: 14,
                      height: 22,
                      border: "1px solid var(--line-2)",
                      background:
                        n <= maxAgents ? "var(--primary)" : "transparent",
                      boxShadow:
                        n <= maxAgents ? "var(--glow-soft)" : "none",
                    }}
                  />
                ))}
              </div>
              <span className="nm-mute" style={{ marginLeft: 8 }}>
                &asymp; {groups} groups
              </span>
            </div>
          </FormRow>

          <FormRow
            label="Label filter"
            hint="Linear label used to pull the issue queue"
          >
            <input
              className="nm-input"
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              style={{ maxWidth: 280 }}
            />
          </FormRow>

          <FormRow
            label="Model"
            hint="default opus · sonnet for speed, haiku for cheap experiments"
          >
            <div className="nm-seg">
              {["opus", "sonnet", "haiku"].map((m) => (
                <button
                  key={m}
                  className={`nm-seg__opt ${model === m ? "nm-seg__opt--active" : ""}`}
                  onClick={() => setModel(m)}
                >
                  {m.toUpperCase()}
                </button>
              ))}
            </div>
          </FormRow>

          <FormRow
            label="Deploy after merge"
            hint="promote dev → staging → prod once all groups complete"
          >
            <button
              className={`nm-toggle ${deploy ? "nm-toggle--on" : ""}`}
              onClick={() => setDeploy(!deploy)}
            >
              <span className="nm-toggle__sw" />
              <span className="nm-toggle__lbl">
                {deploy ? "ENABLED" : "DISABLED"}
              </span>
            </button>
          </FormRow>

          <FormRow
            label="Single-issue override"
            hint="optional · pulls only this issue and ignores label filter"
          >
            <input
              className="nm-input"
              value={override}
              onChange={(e) => setOverride(e.target.value.toUpperCase())}
              placeholder="e.g. ABS-101"
              style={{ maxWidth: 200 }}
            />
          </FormRow>

          <FormRow
            label="Dry run"
            hint="plan + preview only — no agents spawned, no merges"
          >
            <button
              className={`nm-toggle ${dryRun ? "nm-toggle--on" : ""}`}
              onClick={() => setDryRun(!dryRun)}
            >
              <span className="nm-toggle__sw" />
              <span className="nm-toggle__lbl">
                {dryRun ? "ON" : "OFF"}
              </span>
            </button>
          </FormRow>
        </Panel>

        <Panel id="CMD-02" title="EFFECTIVE COMMAND" right={<span>copy</span>}>
          <div
            style={{
              fontSize: 13,
              color: "var(--text)",
              padding: "4px 0",
              wordBreak: "break-word",
            }}
          >
            <span className="nm-mute">$</span>{" "}
            <span className="nm-prim">./scripts/start-night-manager.sh</span>{" "}
            <span>--max-agents {maxAgents}</span>{" "}
            <span>--label &quot;{label}&quot;</span>{" "}
            <span>--model {model}</span>{" "}
            {deploy && <span>--deploy </span>}
            {override && <span>--issue {override} </span>}
            {dryRun && <span>--dry-run</span>}
          </div>
        </Panel>
      </div>

      <div className="nm-col" style={{ gap: 12, minWidth: 0 }}>
        <Panel
          id="PLN-04"
          title="PLANNED EXECUTION"
          flush
        >
          <div
            style={{
              padding: 12,
              display: "flex",
              flexDirection: "column",
              gap: 8,
            }}
          >
            {Array.from({ length: groups }, (_, i) => (
              <div
                key={i}
                style={{
                  border: "1px solid var(--line-2)",
                  padding: "8px 10px",
                  display: "grid",
                  gridTemplateColumns: "70px 1fr",
                  gap: 12,
                  alignItems: "center",
                  background: "var(--bg-2)",
                }}
              >
                <div>
                  <div className="nm-up">group</div>
                  <div
                    className="nm-prim"
                    style={{ fontSize: 18, letterSpacing: "0.04em" }}
                  >
                    G{String(i + 1).padStart(2, "0")}
                  </div>
                </div>
                <div className="nm-row" style={{ gap: 6, flexWrap: "wrap" }}>
                  <span className="nm-pill" style={{ color: "var(--text-dim)" }}>
                    {maxAgents} slots
                  </span>
                </div>
              </div>
            ))}
            {deploy && (
              <div
                style={{
                  border: "1px dashed var(--line-2)",
                  padding: "10px 12px",
                  color: "var(--text-mute)",
                  textAlign: "center",
                  letterSpacing: "0.12em",
                  textTransform: "uppercase",
                  fontSize: 11,
                }}
              >
                &#9650; then DEPLOY &middot; dev &rarr; staging &rarr; prod
              </div>
            )}
          </div>
        </Panel>

        <Panel id="LCH-05" title="" flush>
          <div
            style={{
              padding: 16,
              display: "flex",
              gap: 12,
              alignItems: "center",
              borderTop: "1px solid var(--line)",
            }}
          >
            <div className="nm-grow">
              <div className="nm-up">ready to launch</div>
              <div
                style={{
                  fontSize: 12,
                  color: "var(--text-dim)",
                  marginTop: 2,
                }}
              >
                {groups} groups &middot; {model} &middot;{" "}
                {deploy ? "deploy on" : "no deploy"}
              </div>
            </div>
            <button className="nm-btn nm-btn--ghost">CANCEL</button>
            <button
              className="nm-btn nm-btn--prim"
              onClick={handleLaunch}
              disabled={launching}
            >
              {launching ? "LAUNCHING…" : "▶ INITIATE RUN"}
            </button>
          </div>
        </Panel>
      </div>
    </div>
  );
}

function FormRow({
  label,
  hint,
  children,
}: {
  label: string;
  hint: string;
  children: React.ReactNode;
}) {
  return (
    <div className="nm-form-row">
      <div className="nm-form-row__lbl-grp">
        <span className="nm-form-row__lbl">{label}</span>
        <span className="nm-form-row__hint">{hint}</span>
      </div>
      <div>{children}</div>
    </div>
  );
}
