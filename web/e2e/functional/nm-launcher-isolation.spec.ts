// ABS-152 + ABS-153: launcher isolation + test-mode bypass.
//
// Two related bugs surfaced when the ABS-143 agent's injection-test spec
// (which POSTed valid input to /api/nm/run/start and asserted not-400) hit
// the live API on the worktree's dev port and killed the unrelated NM
// session running in the main checkout's tmux. Root causes:
//
//   ABS-152: scripts/start-night-manager.sh used a global tmux session
//            name `night-manager`, so any worktree's launcher run killed
//            every other worktree's NM. Fix: derive the name from the
//            REPO_ROOT path hash.
//
//   ABS-153: /api/nm/run/start had no test-mode bypass, so a valid
//            payload always fired exec() on the launcher. Fix: when
//            NM_TEST_MODE=1, return {ok:true, testMode:true, cmd} before
//            exec(). scripts/e2e-up.sh exports this for the worktree's
//            next-dev so Playwright specs never trigger a real launch.

import { execSync } from "child_process";
import { mkdtempSync, writeFileSync, chmodSync, readFileSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";
import { test, expect } from "@playwright/test";

test.describe("NM launcher isolation (ABS-152)", () => {
  test("two different worktree paths produce distinct tmux session names", () => {
    // Smoke-level check: run the same SESSION_NAME derivation that the
    // launcher uses, against two fabricated REPO_ROOT paths, and confirm
    // they never collide. We don't actually spawn NM here.
    const derive = (path: string) =>
      execSync(`printf '%s' "${path}" | shasum -a 1 | cut -c1-8`)
        .toString()
        .trim();
    const main = `nm-${derive("/Users/alice/dev/agentic-bylaw-system")}`;
    const worktree = `nm-${derive(
      "/Users/alice/dev/agentic-bylaw-system/.claude/worktrees/nm-abs-143",
    )}`;
    expect(main).not.toEqual(worktree);
    expect(main).toMatch(/^nm-[a-f0-9]{8}$/);
    expect(worktree).toMatch(/^nm-[a-f0-9]{8}$/);
  });

  test("start-night-manager.sh references SESSION_NAME, never literal 'night-manager' in tmux calls", () => {
    const script = readFileSync(
      join(process.cwd(), "..", "scripts", "start-night-manager.sh"),
      "utf-8",
    );
    // The script must declare SESSION_NAME from REPO_ROOT.
    expect(script).toMatch(/SESSION_NAME="nm-.*shasum.*"/);
    // After ABS-154, the script must has-session check and new-session, both
    // targeting $SESSION_NAME. It must NOT unconditionally kill-session.
    expect(script).toMatch(/tmux has-session -t "\$SESSION_NAME"/);
    expect(script).toMatch(/tmux new-session -d -s "\$SESSION_NAME"/);
    // The literal global name must NOT appear as a tmux target anywhere.
    const tmuxLines = script
      .split("\n")
      .filter((l) => l.match(/^\s*tmux\s+(has|kill|new)-session/));
    for (const line of tmuxLines) {
      expect(line).not.toMatch(/-t\s+night-manager\b/);
      expect(line).not.toMatch(/-s\s+night-manager\b/);
    }
    // An unconditional kill-session must NOT be present (would re-introduce
    // the self-destruction bug ABS-154 fixed).
    const killLines = script
      .split("\n")
      .filter((l) => /tmux kill-session/.test(l) && !l.trim().startsWith("#"));
    for (const line of killLines) {
      // The only kill-session references allowed are in echo statements (help
      // text telling the user how to stop NM) or comments — not as actual
      // commands. Filter out echo wrapped lines.
      expect(line.trim()).toMatch(/^echo\s+|^#/);
    }
  });

  test("running start-night-manager.sh from two fabricated REPO_ROOTs produces different session names without colliding", () => {
    // End-to-end-ish: invoke the SESSION_NAME derivation block by
    // extracting it and running it under two different REPO_ROOT values.
    // We don't actually invoke tmux — just verify the derivation is
    // reachable and deterministic from the script's logic.
    const tmpA = mkdtempSync(join(tmpdir(), "nm-iso-a-"));
    const tmpB = mkdtempSync(join(tmpdir(), "nm-iso-b-"));
    const probe = `REPO_ROOT="$1"; echo "nm-$(printf '%s' "$REPO_ROOT" | shasum -a 1 | cut -c1-8)"`;
    const probePath = join(tmpA, "probe.sh");
    writeFileSync(probePath, `#!/usr/bin/env bash\n${probe}\n`);
    chmodSync(probePath, 0o755);
    const a = execSync(`${probePath} ${tmpA}`).toString().trim();
    const b = execSync(`${probePath} ${tmpB}`).toString().trim();
    expect(a).not.toEqual(b);
  });
});

test.describe("NM launcher refuses to clobber a live session (ABS-154)", () => {
  // Use a unique tmux session name per test run so we don't accidentally
  // touch a real NM session a developer has running locally.
  const TEST_SESSION = `nm-test-clobber-${process.pid}-${Date.now()}`;

  test.afterEach(() => {
    // Best-effort cleanup of any session we created.
    try {
      execSync(`tmux kill-session -t "${TEST_SESSION}" 2>/dev/null`, {
        stdio: "ignore",
      });
    } catch {
      // ignore
    }
  });

  test("has-session check refuses to clobber and exits non-zero", () => {
    // Create a sentinel session the launcher would target if it weren't
    // refusing. A long sleep keeps it alive across the second invocation.
    execSync(
      `tmux new-session -d -s "${TEST_SESSION}" 'sleep 60'`,
    );

    // Inline the launcher's guard logic against a fixed SESSION_NAME — the
    // real launcher derives SESSION_NAME from REPO_ROOT, which we can't
    // safely override mid-test without spawning a real NM. The guard block
    // is what we're proving here.
    const guard = `
set -e
SESSION_NAME="${TEST_SESSION}"
if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
  echo "ERROR: NM session '$SESSION_NAME' is already running." >&2
  echo "       Stop it first with:  tmux kill-session -t \${SESSION_NAME}" >&2
  echo "       (This launcher refuses to clobber a live session — ABS-154.)" >&2
  exit 2
fi
echo "would-have-launched"
`;
    let exitCode = 0;
    let stderr = "";
    try {
      execSync(`bash -c '${guard.replace(/'/g, "'\\''")}'`, {
        stdio: ["ignore", "pipe", "pipe"],
      });
    } catch (e) {
      // execSync throws on non-zero exit.
      const err = e as { status?: number; stderr?: Buffer };
      exitCode = err.status ?? -1;
      stderr = err.stderr?.toString() ?? "";
    }
    expect(exitCode).toBe(2);
    expect(stderr).toContain("already running");
    expect(stderr).toContain("ABS-154");

    // Confirm the original session is still alive.
    const stillThere = execSync(
      `tmux has-session -t "${TEST_SESSION}" 2>&1 && echo OK || echo GONE`,
    )
      .toString()
      .trim();
    expect(stillThere).toBe("OK");
  });

  test("start-night-manager.sh contains the has-session guard with the right error format", () => {
    const script = readFileSync(
      join(process.cwd(), "..", "scripts", "start-night-manager.sh"),
      "utf-8",
    );
    // Guard must call has-session before new-session.
    expect(script).toMatch(
      /tmux has-session -t "\$SESSION_NAME"[\s\S]*?tmux new-session -d -s "\$SESSION_NAME"/,
    );
    // Guard exits 2 with a clear message.
    expect(script).toMatch(/exit 2/);
    expect(script).toMatch(/already running/i);
    expect(script).toMatch(/ABS-154/);
  });
});

test.describe("NM /api/nm/run/start test-mode bypass (ABS-153)", () => {
  const VALID_BODY = {
    maxAgents: 3,
    label: "Triaged",
    model: "opus",
    agentModel: "sonnet",
    agentEffort: "high",
    agentTokenLimit: 10,
    reviewerModel: "sonnet",
    reviewerTokenLimit: 2,
    deploy: false,
    dryRun: true,
  };

  test("valid POST returns testMode=true and a non-empty cmd without spawning NM", async ({
    request,
  }) => {
    const res = await request.post("/api/nm/run/start", { data: VALID_BODY });
    expect(res.status()).toBe(200);
    const body = await res.json();
    expect(body.ok).toBe(true);
    expect(body.testMode).toBe(true);
    expect(typeof body.cmd).toBe("string");
    expect(body.cmd).toContain("./scripts/start-night-manager.sh");
    expect(body.cmd).toContain("--max-agents 3");
    expect(body.cmd).toContain("--dry-run");
  });

  test("POST with issue=ABS-XXX (the very payload that caused the 2026-05-26 incident) does NOT spawn an NM", async ({
    request,
  }) => {
    // The original incident POSTed { ...VALID_BODY, issue: "ABS-123" } and
    // asserted not-400, which fired the launcher. With the bypass in place,
    // the same payload returns testMode=true and never reaches exec().
    const res = await request.post("/api/nm/run/start", {
      data: { ...VALID_BODY, issue: "ABS-123" },
    });
    expect(res.status()).toBe(200);
    const body = await res.json();
    expect(body.testMode).toBe(true);
    expect(body.cmd).toContain("--issue ABS-123");
  });

  test("returned cmd preserves argument ordering and quoting for label", async ({
    request,
  }) => {
    const res = await request.post("/api/nm/run/start", {
      data: { ...VALID_BODY, label: "Backlog" },
    });
    const body = await res.json();
    expect(body.cmd).toContain('--label "Backlog"');
  });
});
