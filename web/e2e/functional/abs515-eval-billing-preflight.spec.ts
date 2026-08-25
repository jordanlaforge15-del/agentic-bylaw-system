// Functional: ABS-515 — an eval run cannot spend money without being asked
//
// An eight-case sweep billed ~$1.70 that nobody had agreed to spend. Nothing
// threw: scripts/run_test_prompts.py drives whatever advisor is listening on
// a port, and until now had no way to ask how that process was configured.
// A metered advisor and a free one were indistinguishable from the caller's
// side, so "did this run cost money?" was answerable only by the invoice.
//
// The ticket's original fix — default eval runs to the `claude_code`
// provider — is gone. ABS-522 removed that backend (0/8 golden passes to the
// API backend's 3/8 on an otherwise identical run). There is no cheap
// provider to fall back to, so what this spec protects is the *consent*:
//
//   1. GET /healthz reports the gateway the advisor actually built and
//      whether it bills per token. Contract asserted against the live e2e
//      advisor, which serves the mock gateway.
//   2. The runner refuses a metered advisor without --allow-metered, and
//      refuses BEFORE it creates the run directory — i.e. before the first
//      turn, which is the only moment the refusal is worth anything.
//   3. Both unknowns fail closed: an advisor too old to report llm.metered,
//      and one whose provider name means nothing to us.
//   4. The abort names ABS-522, so nobody spends an afternoon looking for
//      the subscription backend that was deleted.
//   5. `make advisor-eval` exists, pins the provider explicitly, and does
//      NOT source .env with `set -a` — the trap that turned a file-scoped
//      key into an inheritable one and left the provider at its metered
//      default.
//
// tests/scripts/test_run_test_prompts_preflight.py pins the decision
// function on hand-built health documents. That is the right shape for the
// branch and blind to the two things that decide a real run: whether the
// runner actually *calls* /healthz before spending, and whether a refusal
// exits non-zero instead of printing a warning and carrying on. Both are
// only observable from outside the process, so this drives the real CLI
// through spawnSync against a stub advisor — no stack, no API key, no spend.

import { spawnSync } from "child_process";
import * as http from "http";
import type { AddressInfo } from "net";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { E2E_API_URL, expect, test } from "../fixtures/test-env";

const REPO_ROOT = path.resolve(__dirname, "../../../");
const RUNNER = path.join(REPO_ROOT, "scripts", "run_test_prompts.py");
const VENV_PYTHON = path.join(REPO_ROOT, ".venv", "bin", "python");
const LAUNCHER = path.join(REPO_ROOT, "scripts", "advisor-eval.sh");

type Health = Record<string, unknown>;

/**
 * A stand-in advisor that serves one canned /healthz and refuses
 * everything else. `chatHits` counts POSTs to /v1/chat — the runner's
 * spend, in the only currency this spec can observe.
 */
async function stubAdvisor(health: Health): Promise<{
  baseUrl: string;
  chatHits: () => number;
  close: () => Promise<void>;
}> {
  let chatHits = 0;
  const server = http.createServer((req, res) => {
    if (req.url === "/healthz") {
      res.writeHead(200, { "content-type": "application/json" });
      res.end(JSON.stringify(health));
      return;
    }
    if (req.url === "/v1/chat") {
      chatHits += 1;
      res.writeHead(503, { "content-type": "text/plain" });
      res.end("stub advisor does not answer");
      return;
    }
    res.writeHead(404);
    res.end();
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address() as AddressInfo;
  return {
    baseUrl: `http://127.0.0.1:${port}`,
    chatHits: () => chatHits,
    close: () =>
      new Promise<void>((resolve) => {
        server.close(() => resolve());
      }),
  };
}

function runRunner(baseUrl: string, extraArgs: string[], outDir: string) {
  const result = spawnSync(
    VENV_PYTHON,
    [
      RUNNER,
      "--base-url",
      baseUrl,
      "--ids",
      "TC-001",
      "--out-dir",
      outDir,
      "--turn-timeout",
      "5",
      ...extraArgs,
    ],
    { cwd: REPO_ROOT, encoding: "utf-8", timeout: 90_000 },
  );
  return {
    status: result.status ?? -1,
    stderr: result.stderr ?? "",
    stdout: result.stdout ?? "",
  };
}

function tmpOutDir(): string {
  // Deliberately a path that does NOT exist yet: its absence after a
  // refusal is the evidence that the runner bailed before doing anything.
  return path.join(
    fs.mkdtempSync(path.join(os.tmpdir(), "abs515-")),
    "run",
  );
}

const METERED: Health = {
  status: "ok",
  llm: { main_model: "claude-opus-4-5", provider: "anthropic", metered: true },
};

const UNMETERED: Health = {
  status: "ok",
  llm: { main_model: "claude-opus-4-5", provider: "mock", metered: false },
};

// Each case spawns a Python interpreter that imports httpx (~1s warm).
test.describe.configure({ timeout: 120_000 });

test.describe("ABS-515 eval-run billing pre-flight", () => {
  test("a metered advisor is refused, before a single turn is sent", async () => {
    const advisor = await stubAdvisor(METERED);
    const outDir = tmpOutDir();
    try {
      const run = runRunner(advisor.baseUrl, [], outDir);

      expect(run.status, run.stderr).not.toBe(0);
      expect(run.stderr).toContain("billing precondition");
      expect(run.stderr).toContain("--allow-metered");
      expect(run.stderr).toContain("anthropic");

      // The refusal is only worth something if it lands ahead of the
      // spend. Nothing was asked of /v1/chat, and the run directory the
      // runner creates on its way to the first turn does not exist.
      expect(advisor.chatHits()).toBe(0);
      expect(fs.existsSync(outDir)).toBe(false);
    } finally {
      await advisor.close();
    }
  });

  test("the refusal says the cheap provider is gone, not 'use the other one'", async () => {
    // The obvious reading of "this would spend metered credits" is "then
    // point me at the free one". ABS-522 deleted it. An abort that doesn't
    // say so sends the operator looking for a backend that no longer
    // exists — the flag is the answer, and it has to be in the message.
    const advisor = await stubAdvisor(METERED);
    try {
      const run = runRunner(advisor.baseUrl, [], tmpOutDir());

      expect(run.stderr).toContain("ABS-522");
      expect(run.stderr).toContain("consent gate");
    } finally {
      await advisor.close();
    }
  });

  test("--allow-metered lets the same run through", async () => {
    const advisor = await stubAdvisor(METERED);
    const outDir = tmpOutDir();
    try {
      const run = runRunner(advisor.baseUrl, ["--allow-metered"], outDir);

      expect(run.status, run.stderr).toBe(0);
      expect(run.stderr).toContain("billing precondition OK");
      // It got past the gate and actually tried to work: the stub 503s
      // every turn, which the runner records rather than crashing on.
      expect(advisor.chatHits()).toBeGreaterThan(0);
      expect(fs.existsSync(path.join(outDir, "SUMMARY.json"))).toBe(true);
    } finally {
      await advisor.close();
    }
  });

  test("an unmetered advisor needs no flag — there is nothing to consent to", async () => {
    const advisor = await stubAdvisor(UNMETERED);
    const outDir = tmpOutDir();
    try {
      const run = runRunner(advisor.baseUrl, [], outDir);

      expect(run.status, run.stderr).toBe(0);
      expect(run.stderr).toContain("provider='mock'");
      expect(run.stderr).toContain("metered=False");
      expect(advisor.chatHits()).toBeGreaterThan(0);
    } finally {
      await advisor.close();
    }
  });

  test("an advisor that cannot report its billing is assumed to be metered", async () => {
    // Every advisor predating ABS-515 lands here. They are overwhelmingly
    // real, metered ones — the mock gateway only runs inside the e2e
    // stack, which is always current — so "I don't know" must resolve to
    // "assume it costs money".
    const advisor = await stubAdvisor({
      status: "ok",
      llm: { main_model: "claude-opus-4-5" },
    });
    try {
      const run = runRunner(advisor.baseUrl, [], tmpOutDir());

      expect(run.status, run.stderr).not.toBe(0);
      expect(run.stderr).toContain("predates");
      expect(advisor.chatHits()).toBe(0);
    } finally {
      await advisor.close();
    }
  });

  test("an unreachable advisor aborts with the command that starts one", async () => {
    // Port 1 is reserved and nothing listens there.
    const run = runRunner("http://127.0.0.1:1", [], tmpOutDir());

    expect(run.status).not.toBe(0);
    expect(run.stderr).toContain("pre-flight");
    expect(run.stderr).toContain("make advisor-eval");
  });

  test("--model still gates on top of the billing check", async () => {
    // ABS-267's pre-flight moved into a function during this change. Both
    // now read one /healthz response; neither may have lost its teeth.
    const advisor = await stubAdvisor(METERED);
    try {
      const run = runRunner(
        advisor.baseUrl,
        ["--allow-metered", "--model", "claude-haiku-4-5"],
        tmpOutDir(),
      );

      expect(run.status, run.stderr).not.toBe(0);
      expect(run.stderr).toContain("--model precondition");
      expect(run.stderr).toContain("claude-opus-4-5");
      expect(advisor.chatHits()).toBe(0);
    } finally {
      await advisor.close();
    }
  });
});

test.describe("ABS-515 the advisor side of the handshake", () => {
  test("the live advisor reports the gateway it built, and its billing", async ({
    request,
  }) => {
    const response = await request.get(`${E2E_API_URL}/healthz`);
    expect(response.ok()).toBe(true);
    const llm = (await response.json()).llm as Record<string, unknown>;

    // The e2e stack serves MockGateway. `provider` follows the object the
    // process built, not ADVISOR_LLM_PROVIDER — an advisor answering from
    // the env var would report `anthropic` here and send the runner
    // looking for consent it doesn't need, and (run the other way) would
    // wave a genuinely metered advisor through as free.
    expect(llm.provider).toBe("mock");
    expect(llm.metered).toBe(false);
    // ABS-267's field is unchanged; the runner reads both from one call.
    expect(typeof llm.main_model).toBe("string");
  });
});

test.describe("ABS-515 the documented way to start an eval advisor", () => {
  const launcher = () => fs.readFileSync(LAUNCHER, "utf-8");

  test("make advisor-eval runs the launcher", () => {
    const makefile = fs.readFileSync(path.join(REPO_ROOT, "Makefile"), "utf-8");

    expect(makefile).toContain("advisor-eval:");
    expect(makefile).toContain("./scripts/advisor-eval.sh");
    expect(fs.existsSync(LAUNCHER)).toBe(true);
    // eslint-disable-next-line no-bitwise
    expect(fs.statSync(LAUNCHER).mode & 0o111).toBeGreaterThan(0);
  });

  test("the launcher pins the provider instead of inheriting it", () => {
    // The incident's advisor came up metered because nothing set the
    // provider and nothing printed it. Both halves are fixed here.
    expect(launcher()).toContain("ADVISOR_LLM_PROVIDER=anthropic");
    expect(launcher()).toContain("--allow-metered");
  });

  test("the launcher does not source .env with `set -a`", () => {
    // This is the trap, stated as a test. `set -a; . ./.env; set +a`
    // exports every name in the file: it leaves ADVISOR_LLM_PROVIDER at
    // its metered default AND promotes ANTHROPIC_API_KEY from a
    // file-scoped value into an inheritable process-environment one.
    // scripts/dev-up.sh may keep doing it — that is the manual-testing
    // stack, and it is not what an eval runner is pointed at.
    expect(launcher()).not.toMatch(/set\s+-a/);
    expect(launcher()).toContain("env -i");
  });

  test("the trap is written down where someone starting an advisor reads it", () => {
    const docs = [
      path.join(REPO_ROOT, "docs", "TEST_PROMPT_GENERATION.md"),
      path.join(REPO_ROOT, "docs", "E2E_TESTING.md"),
    ].map((p) => fs.readFileSync(p, "utf-8"));

    for (const doc of docs) {
      expect(doc).toContain("set -a");
      expect(doc).toContain("make advisor-eval");
      expect(doc).toContain("--allow-metered");
    }
  });
});
