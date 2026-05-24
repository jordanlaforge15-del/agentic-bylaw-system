import { readFile } from "fs/promises";
import { join } from "path";

const STATE_PATH = join(process.cwd(), "..", ".night-manager", "state.json");

export async function GET() {
  try {
    const raw = await readFile(STATE_PATH, "utf-8");
    const state = JSON.parse(raw);
    return Response.json(state);
  } catch {
    return Response.json(null);
  }
}
