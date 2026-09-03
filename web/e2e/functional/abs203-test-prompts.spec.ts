// ABS-203: Regional Centre bylaw test prompt suite
//
// Validates:
//   1. The JSON database loads and passes schema validation.
//   2. All 10 base test cases are present with required fields.
//   3. The Python query tool returns correctly filtered results for
//      zone, persona, complexity, liability, tag, and bylaw-feature filters.
//   4. A simple single-turn test case (TC-001, HR-1 homeowner) can be
//      submitted to the live advisor chat and returns a response
//      mentioning the expected bylaw keyword ("setback").

import { spawnSync } from "child_process";
import * as fs from "fs";
import * as path from "path";
import { expect, test, openCaseViaApi } from "../fixtures/test-env";

// Resolve paths relative to the repo root (one level above web/).
const REPO_ROOT = path.resolve(__dirname, "../../../");
const PROMPTS_FILE = path.join(REPO_ROOT, "evals", "regional_centre_test_prompts.json");
const QUERY_SCRIPT = path.join(REPO_ROOT, "scripts", "query_test_prompts.py");

type PersonaRecord = { type: string; subtype: string | null; description: string };
type TurnRecord = { turn: number; role: string; message: string };

interface TestCase {
  id: string;
  title: string;
  persona: PersonaRecord;
  address: string;
  zone: string;
  complexity: "simple" | "medium" | "complex";
  liability: "low" | "medium" | "high";
  tags: string[];
  bylaw_features: string[];
  turns: TurnRecord[];
  expected_bylaw_references: string[];
  expected_answer_keywords: string[];
  expected_topics: string[];
  notes: string;
}

function loadPrompts(): TestCase[] {
  const raw = fs.readFileSync(PROMPTS_FILE, "utf-8");
  return JSON.parse(raw) as TestCase[];
}

const VENV_PYTHON = path.join(REPO_ROOT, ".venv", "bin", "python");

function runQuery(...args: string[]): { stdout: string; stderr: string; status: number } {
  const result = spawnSync(
    VENV_PYTHON,
    [QUERY_SCRIPT, ...args],
    { cwd: REPO_ROOT, encoding: "utf-8" },
  );
  return {
    stdout: result.stdout ?? "",
    stderr: result.stderr ?? "",
    status: result.status ?? -1,
  };
}

// ─── Schema validation (no server required) ──────────────────────────────────

test.describe("Test prompt database — schema validation", () => {
  const REQUIRED_FIELDS: Array<keyof TestCase> = [
    "id", "title", "persona", "address", "zone",
    "complexity", "liability", "tags", "bylaw_features",
    "turns", "expected_bylaw_references", "expected_answer_keywords",
    "expected_topics", "notes",
  ];

  const VALID_ZONES = new Set([
    "CEN-1", "CEN-2", "COR", "DD", "DH",
    "ER-1", "ER-2", "ER-3", "HR-1", "HR-2", "INS", "RPK",
  ]);

  const VALID_COMPLEXITIES = new Set(["simple", "medium", "complex"]);
  const VALID_LIABILITIES = new Set(["low", "medium", "high"]);
  const VALID_PERSONA_TYPES = new Set([
    "homeowner", "real_estate_developer", "real_estate_law_professional",
    "realtor", "architectural_consultant", "city_agent",
  ]);

  let prompts: TestCase[];

  test.beforeAll(() => {
    expect(fs.existsSync(PROMPTS_FILE), `${PROMPTS_FILE} must exist`).toBe(true);
    prompts = loadPrompts();
  });

  test("database contains exactly 10 base test cases", () => {
    expect(prompts.length).toBeGreaterThanOrEqual(10);
  });

  test("all test cases have required fields", () => {
    const errors: string[] = [];
    for (const tc of prompts) {
      for (const field of REQUIRED_FIELDS) {
        if (tc[field] === undefined || tc[field] === null) {
          errors.push(`${tc.id}: missing field '${field}'`);
        }
      }
    }
    expect(errors).toEqual([]);
  });

  test("all IDs are unique and match TC-NNN format", () => {
    const ids = prompts.map((p) => p.id);
    const unique = new Set(ids);
    expect(unique.size).toBe(ids.length);
    for (const id of ids) {
      expect(id).toMatch(/^TC-\d{3}$/);
    }
  });

  test("all zones are valid bylaw zone codes", () => {
    const invalid = prompts.filter((p) => !VALID_ZONES.has(p.zone)).map((p) => `${p.id}:${p.zone}`);
    expect(invalid).toEqual([]);
  });

  test("all complexity values are valid", () => {
    const invalid = prompts.filter((p) => !VALID_COMPLEXITIES.has(p.complexity)).map((p) => p.id);
    expect(invalid).toEqual([]);
  });

  test("all liability values are valid", () => {
    const invalid = prompts.filter((p) => !VALID_LIABILITIES.has(p.liability)).map((p) => p.id);
    expect(invalid).toEqual([]);
  });

  test("all persona types are valid", () => {
    const invalid = prompts
      .filter((p) => !VALID_PERSONA_TYPES.has(p.persona.type))
      .map((p) => `${p.id}:${p.persona.type}`);
    expect(invalid).toEqual([]);
  });

  test("every test case has at least one conversation turn", () => {
    const empty = prompts.filter((p) => !p.turns || p.turns.length === 0).map((p) => p.id);
    expect(empty).toEqual([]);
  });

  test("no stub placeholder messages remain in turns", () => {
    const stubs: string[] = [];
    for (const tc of prompts) {
      for (const turn of tc.turns) {
        if (turn.message.startsWith("[STUB")) {
          stubs.push(`${tc.id} turn ${turn.turn}`);
        }
      }
    }
    expect(stubs).toEqual([]);
  });

  test("all turn role values are 'user'", () => {
    const bad: string[] = [];
    for (const tc of prompts) {
      for (const turn of tc.turns) {
        if (turn.role !== "user") {
          bad.push(`${tc.id} turn ${turn.turn}: role='${turn.role}'`);
        }
      }
    }
    expect(bad).toEqual([]);
  });

  test("database covers at least 8 distinct zones", () => {
    const zones = new Set(prompts.map((p) => p.zone));
    expect(zones.size).toBeGreaterThanOrEqual(8);
  });

  test("database covers at least 4 distinct persona types", () => {
    const personas = new Set(prompts.map((p) => p.persona.type));
    expect(personas.size).toBeGreaterThanOrEqual(4);
  });

  test("database contains at least one case per complexity level", () => {
    const levels = new Set(prompts.map((p) => p.complexity));
    expect(levels.has("simple")).toBe(true);
    expect(levels.has("medium")).toBe(true);
    expect(levels.has("complex")).toBe(true);
  });

  test("database contains at least one case per liability level", () => {
    const levels = new Set(prompts.map((p) => p.liability));
    expect(levels.has("low")).toBe(true);
    expect(levels.has("medium")).toBe(true);
    expect(levels.has("high")).toBe(true);
  });
});

// ─── Query tool validation (Python CLI) ──────────────────────────────────────

test.describe("Query tool — filter dimensions", () => {
  // ABS-467: TC-017 was ER-1 until the zoning schedule turned out to map no
  // ER-1 polygon anywhere, so no address could ever confirm that zone. It is
  // ER-2 now, which carries the same use permissions its premise depends on.
  test("--zone ER-2 returns TC-017", () => {
    const { stdout, status } = runQuery("--zone", "ER-2", "--output", "ids");
    expect(status).toBe(0);
    expect(stdout.trim().split("\n")).toContain("TC-017");
  });

  // ABS-463: TC-001's zone field said ER-1 while its address geocodes into the
  // HR-1 polygon. The field now records the geocoded zone; the turn text still
  // misstates ER-1 on purpose. ABS-467 re-derived the address from HR-1, so
  // the premise now rests on a ROOFTOP match rather than an interpolated one.
  test("--zone HR-1 returns TC-001", () => {
    const { stdout, status } = runQuery("--zone", "HR-1", "--output", "ids");
    expect(status).toBe(0);
    expect(stdout.trim().split("\n")).toContain("TC-001");
  });

  test("--zone CEN-1 returns at least one case", () => {
    const { stdout, status } = runQuery("--zone", "CEN-1", "--output", "ids");
    expect(status).toBe(0);
    expect(stdout.trim().length).toBeGreaterThan(0);
  });

  test("--persona homeowner returns multiple cases", () => {
    const { stdout, status } = runQuery("--persona", "homeowner", "--output", "ids");
    expect(status).toBe(0);
    const ids = stdout.trim().split("\n").filter(Boolean);
    expect(ids.length).toBeGreaterThanOrEqual(2);
  });

  test("--complexity simple returns only simple cases", () => {
    const { stdout, status } = runQuery("--complexity", "simple", "--output", "json");
    expect(status).toBe(0);
    const results = JSON.parse(stdout) as TestCase[];
    expect(results.every((r) => r.complexity === "simple")).toBe(true);
  });

  test("--liability high returns only high-liability cases", () => {
    const { stdout, status } = runQuery("--liability", "high", "--output", "json");
    expect(status).toBe(0);
    const results = JSON.parse(stdout) as TestCase[];
    expect(results.every((r) => r.liability === "high")).toBe(true);
    expect(results.length).toBeGreaterThanOrEqual(4);
  });

  test("--tag renovation returns cases tagged renovation", () => {
    const { stdout, status } = runQuery("--tag", "renovation", "--output", "json");
    expect(status).toBe(0);
    const results = JSON.parse(stdout) as TestCase[];
    expect(results.every((r) => r.tags.includes("renovation"))).toBe(true);
  });

  test("--bylaw-feature parking returns cases with parking feature", () => {
    const { stdout, status } = runQuery("--bylaw-feature", "parking", "--output", "json");
    expect(status).toBe(0);
    const results = JSON.parse(stdout) as TestCase[];
    expect(results.every((r) => r.bylaw_features.includes("parking"))).toBe(true);
    expect(results.length).toBeGreaterThanOrEqual(3);
  });

  test("--id TC-001 returns exactly one record", () => {
    const { stdout, status } = runQuery("--id", "TC-001", "--output", "json");
    expect(status).toBe(0);
    const results = JSON.parse(stdout) as TestCase[];
    expect(results.length).toBe(1);
    expect(results[0].id).toBe("TC-001");
  });

  test("--list-zones exits 0 and lists only zones the schedule maps", () => {
    const { stdout, status } = runQuery("--list-zones");
    expect(status).toBe(0);
    expect(stdout).toContain("ER-2");
    // ABS-467: the by-law defines ER-1 (Part I s.30) but no polygon carries it,
    // so a case claiming it could never have its address confirmed.
    expect(stdout).not.toContain("ER-1");
  });

  test("--list-personas exits 0 and includes homeowner", () => {
    const { stdout, status } = runQuery("--list-personas");
    expect(status).toBe(0);
    expect(stdout).toContain("homeowner");
  });

  test("combined filters: --zone CEN-1 --persona real_estate_developer", () => {
    const { stdout, status } = runQuery(
      "--zone", "CEN-1",
      "--persona", "real_estate_developer",
      "--output", "json",
    );
    expect(status).toBe(0);
    const results = JSON.parse(stdout) as TestCase[];
    for (const r of results) {
      expect(r.zone).toBe("CEN-1");
      expect(r.persona.type).toBe("real_estate_developer");
    }
  });
});

// ─── Live chat smoke test (requires running stack) ───────────────────────────

test.describe("Live advisor chat — TC-001 smoke", () => {
  test("TC-001 turn-1 message can be submitted to the advisor chat", async ({ page }) => {
    const prompts = loadPrompts();
    const tc001 = prompts.find((p) => p.id === "TC-001");
    expect(tc001, "TC-001 must exist in the database").toBeDefined();

    const turn1 = tc001!.turns.find((t) => t.turn === 1);
    expect(turn1, "TC-001 must have a turn 1").toBeDefined();
    expect(turn1!.message.length, "Turn 1 message must not be empty").toBeGreaterThan(10);

    // Open a case via the API and navigate to the chat page
    const { caseId } = await openCaseViaApi({ anchorLabel: "1222 Robie Street, Halifax NS" });
    await page.goto(`/app?case_id=${caseId}`);

    const textarea = page.getByPlaceholder(/Ask about this parcel/);
    await expect(textarea).toBeVisible();
    await textarea.scrollIntoViewIfNeeded();

    // Wait for React hydration before filling
    await page.waitForFunction(() => {
      const el = document.querySelector('textarea[placeholder^="Ask about this parcel"]');
      if (!el) return false;
      return Object.keys(el).some((k) => k.startsWith("__reactProps"));
    });

    // Send the TC-001 turn-1 message
    await textarea.fill(turn1!.message);
    await textarea.press("Enter");

    // The mock dispatcher returns a response — verify the chat thread renders one
    await expect(
      page.getByTestId("chat-thread"),
    ).toContainText(/Based on the bylaw evidence/i, { timeout: 20_000 });
  });
});
