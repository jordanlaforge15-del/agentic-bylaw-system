import { execSync } from "child_process";
import { join } from "path";

let _repoRoot: string | null = null;

function repoRoot(): string {
  if (_repoRoot) return _repoRoot;
  try {
    const gitCommon = execSync("git rev-parse --git-common-dir", {
      cwd: process.cwd(),
      encoding: "utf-8",
    }).trim();
    _repoRoot = join(gitCommon, "..");
  } catch {
    _repoRoot = join(process.cwd(), "..");
  }
  return _repoRoot;
}

export const NM_DIR = () => join(repoRoot(), ".night-manager");
export const STATE_PATH = () => join(NM_DIR(), "state.json");
export const LOGS_DIR = () => join(NM_DIR(), "logs");
export const LOG_PATH = () => join(NM_DIR(), "night_manager.log");
export const PROJECT_ROOT = () => repoRoot();
