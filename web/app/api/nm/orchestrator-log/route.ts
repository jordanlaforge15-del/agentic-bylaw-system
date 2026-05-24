import { readFile } from "fs/promises";
import { join } from "path";

const LOG_PATH = join(process.cwd(), "..", ".night-manager", "night_manager.log");

export async function GET() {
  try {
    const raw = await readFile(LOG_PATH, "utf-8");
    const lines = raw
      .trim()
      .split("\n")
      .filter(Boolean)
      .map(parseLine)
      .filter(Boolean)
      .slice(-100);
    return Response.json(lines);
  } catch {
    return Response.json([]);
  }
}

function parseLine(line: string) {
  const m = line.match(
    /^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+\]\s+\S+\s+(INFO|WARNING|ERROR|DEBUG):\s+(.*)$/,
  );
  if (!m) return null;
  const [, ts, levelRaw, msg] = m;
  const level =
    levelRaw === "ERROR"
      ? "err"
      : levelRaw === "WARNING"
        ? "warn"
        : msg.includes("merged") || msg.includes("Merged")
          ? "ok"
          : "info";
  const time = ts.split(" ")[1] ?? ts;
  return { t: time, level, msg };
}
