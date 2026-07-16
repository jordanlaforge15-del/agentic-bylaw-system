"use client";

import { useState, useMemo, useEffect } from "react";
import { useRouter } from "next/navigation";
import { Panel } from "../components/panel";
import type { RunState, Issue } from "../lib/types";

export default function LaunchPage() {
  const router = useRouter();
  const [maxAgents, setMaxAgents] = useState(3);
  const [label, setLabel] = useState("Triaged");
  const [model, setModel] = useState("opus");
  const [agentModel, setAgentModel] = useState("opus");
  const [agentEffort, setAgentEffort] = useState("high");
  const [agentTokenLimit, setAgentTokenLimit] = useState(10);
  const [reviewerModel, setReviewerModel] = useState("sonnet");
  const [reviewerTokenLimit, setReviewerTokenLimit] = useState(2);
  const [deploy, setDeploy] = useState(true);
  const [dryRun, setDryRun] = useState(false);
  const [override, setOverride] = useState("");
  const [launching, setLaunching] = useState(false);
  const [lastRun, setLastRun] = useState<RunState | null>(null);
  const [resumeQueued, setResumeQueued] = useState(false);

  const groups = useMemo(() => {
    const count = override ? 1 : 9;
    return Math.ceil(count / maxAgents);
  }, [maxAgents, override]);

  useEffect(() => {
    fetch("/api/nm/state")
      .then((r) => r.json())
      .then((data: RunState) => {
        if (data.run_id) setLastRun(data);
      })
      .catch(() => {});
  }, []);

  const resumableIssues = useMemo(() => {
    if (!lastRun) return [];
    const allowed = resumeQueued
      ? ["failed", "blocked", "reviewing", "queued"]
      : ["failed", "blocked", "reviewing"];
    return Object.values(lastRun.issues).filter((i) =>
      allowed.includes(i.status),
    );
  }, [lastRun, resumeQueued]);

  const hasQueued = useMemo(() => {
    if (!lastRun) return false;
    return Object.values(lastRun.issues).some((i) => i.status === "queued");
  }, [lastRun]);

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
          agentModel,
          agentEffort,
          agentTokenLimit,
          reviewerModel,
          reviewerTokenLimit,
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

  async function handleResume(issueId?: string) {
    setLaunching(true);
    try {
      await fetch("/api/nm/run/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          maxAgents,
          model,
          agentModel,
          agentEffort,
          agentTokenLimit,
          reviewerModel,
          reviewerTokenLimit,
          deploy,
          resume: !issueId,
          resumeIssue: issueId,
          resumeQueued,
        }),
      });
      router.push("/nm");
    } catch {
      setLaunching(false);
    }
  }

  return (
    <div className="nm-launch">
      <div className="nm-col nm-col--min-0">
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
            <div className="nm-row nm-row--gap-14">
              <input
                className="nm-input nm-input-narrow"
                type="number"
                min={1}
                max={10}
                value={maxAgents}
                onChange={(e) =>
                  setMaxAgents(
                    Math.max(1, Math.min(10, +e.target.value || 1)),
                  )
                }
              />
              <div className="nm-row nm-agent-slots">
                {[1, 2, 3, 4, 5, 6, 7, 8].map((n) => (
                  <span
                    key={n}
                    className={`nm-agent-slot ${n <= maxAgents ? "nm-agent-slot--active" : ""}`}
                  />
                ))}
              </div>
              <span className="nm-mute nm-right">
                &asymp; {groups} groups
              </span>
            </div>
          </FormRow>

          <FormRow
            label="Label filter"
            hint="Linear label used to pull the issue queue"
          >
            <input
              className="nm-input nm-input-label"
              value={label}
              onChange={(e) => setLabel(e.target.value)}
            />
          </FormRow>

          <FormRow
            label="Agent model"
            hint="Claude model for dev agents that implement issues"
          >
            <div className="nm-seg">
              {["opus", "sonnet", "haiku"].map((m) => (
                <button
                  key={m}
                  className={`nm-seg__opt ${agentModel === m ? "nm-seg__opt--active" : ""}`}
                  onClick={() => setAgentModel(m)}
                >
                  {m.toUpperCase()}
                </button>
              ))}
            </div>
          </FormRow>

          <FormRow
            label="Agent effort"
            hint="low = fast/cheap · medium = balanced · high = thorough"
          >
            <div className="nm-seg">
              {["low", "medium", "high"].map((e) => (
                <button
                  key={e}
                  className={`nm-seg__opt ${agentEffort === e ? "nm-seg__opt--active" : ""}`}
                  onClick={() => setAgentEffort(e)}
                >
                  {e.toUpperCase()}
                </button>
              ))}
            </div>
          </FormRow>

          <FormRow
            label="Agent token limit"
            hint="estimated-USD cap per agent — Claude Code --max-budget-usd"
          >
            <input
              className="nm-input nm-input-narrow"
              type="number"
              min={1}
              max={50}
              step={1}
              value={agentTokenLimit}
              onChange={(e) => setAgentTokenLimit(Math.max(1, +e.target.value || 1))}
            />
          </FormRow>

          <FormRow
            label="Reviewer model"
            hint="Claude model for code review gate"
          >
            <div className="nm-seg">
              {["opus", "sonnet", "haiku"].map((m) => (
                <button
                  key={m}
                  className={`nm-seg__opt ${reviewerModel === m ? "nm-seg__opt--active" : ""}`}
                  onClick={() => setReviewerModel(m)}
                >
                  {m.toUpperCase()}
                </button>
              ))}
            </div>
          </FormRow>

          <FormRow
            label="Reviewer token limit"
            hint="estimated-USD cap per review — Claude Code --max-budget-usd"
          >
            <input
              className="nm-input nm-input-narrow"
              type="number"
              min={0.5}
              max={20}
              step={0.5}
              value={reviewerTokenLimit}
              onChange={(e) => setReviewerTokenLimit(Math.max(0.5, +e.target.value || 0.5))}
            />
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
              className="nm-input nm-input-override"
              value={override}
              onChange={(e) => setOverride(e.target.value.toUpperCase())}
              placeholder="e.g. ABS-101"
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
          <div className="nm-cmd-display">
            <span className="nm-mute">$</span>{" "}
            <span className="nm-prim">./scripts/start-night-manager.sh</span>{" "}
            <span>--max-agents {maxAgents}</span>{" "}
            <span>--label &quot;{label}&quot;</span>{" "}
            <span>--agent-model {agentModel}</span>{" "}
            <span>--agent-effort {agentEffort}</span>{" "}
            <span>--agent-token-limit {agentTokenLimit}</span>{" "}
            <span>--reviewer-model {reviewerModel}</span>{" "}
            <span>--reviewer-token-limit {reviewerTokenLimit}</span>{" "}
            {deploy && <span>--deploy </span>}
            {override && <span>--issue {override} </span>}
            {dryRun && <span>--dry-run</span>}
          </div>
        </Panel>
      </div>

      <div className="nm-col nm-col--gap-12 nm-col--min-0">
        <Panel
          id="PLN-04"
          title="PLANNED EXECUTION"
          flush
        >
          <div className="nm-planned-container">
            {Array.from({ length: groups }, (_, i) => (
              <div key={i} className="nm-group-row">
                <div>
                  <div className="nm-up">group</div>
                  <div className="nm-prim nm-group-num">
                    G{String(i + 1).padStart(2, "0")}
                  </div>
                </div>
                <div className="nm-row nm-row--gap-6 nm-row--wrap">
                  <span className="nm-pill nm-dim">
                    {maxAgents} slots
                  </span>
                </div>
              </div>
            ))}
            {deploy && (
              <div className="nm-deploy-row">
                &#9650; then DEPLOY &middot; dev &rarr; staging &rarr; prod
              </div>
            )}
          </div>
        </Panel>

        {lastRun && (resumableIssues.length > 0 || hasQueued) && (
          <Panel
            id="RSM-03"
            title="RESUME LAST RUN"
            right={<span>{lastRun.run_id}</span>}
            flush
          >
            <div className="nm-resume-section">
              <div className="nm-resume-header">
                {resumableIssues.length} failed/blocked issue
                {resumableIssues.length !== 1 ? "s" : ""} from last run
              </div>
              {resumableIssues.map((iss) => (
                <div
                  key={iss.identifier}
                  data-testid={`resume-row-${iss.identifier}`}
                  className="nm-resume-row"
                >
                  <span className="nm-prim nm-resume-row__id">
                    {iss.identifier}
                  </span>
                  <span className="nm-mute nm-resume-row__title">
                    {iss.title}
                  </span>
                  <span
                    className={`nm-pill nm-resume-row__status ${
                      iss.status === "failed" ? "nm-err" : "nm-warn"
                    }`}
                  >
                    {iss.status}
                  </span>
                  <button
                    className="nm-btn nm-btn--ghost nm-resume-row__button"
                    onClick={() => handleResume(iss.identifier)}
                    disabled={launching}
                  >
                    RESUME
                  </button>
                </div>
              ))}
              {hasQueued && (
                <label
                  data-testid="resume-queued-toggle"
                  className="nm-resume-checkbox"
                >
                  <input
                    type="checkbox"
                    checked={resumeQueued}
                    onChange={(e) => setResumeQueued(e.target.checked)}
                  />
                  Include queued (never-started) issues
                </label>
              )}
              <div className="nm-resume-actions">
                <button
                  className="nm-btn nm-btn--prim nm-resume-button-full"
                  onClick={() => handleResume()}
                  disabled={launching || resumableIssues.length === 0}
                >
                  {launching
                    ? "RESUMING…"
                    : resumableIssues.length === 0
                      ? "▶ NOTHING TO RESUME"
                      : `▶ RESUME ALL ${resumableIssues.length} ISSUES`}
                </button>
              </div>
            </div>
          </Panel>
        )}

        <Panel id="LCH-05" title="" flush>
          <div className="nm-row nm-launch-footer">
            <div className="nm-grow">
              <div className="nm-up">ready to launch</div>
              <div className="nm-dim nm-mt-1">
                {groups} groups &middot; {agentModel}/{agentEffort} &middot;{" "}
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
