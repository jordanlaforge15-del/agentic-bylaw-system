import { readdir, stat } from "fs/promises";
import { join } from "path";
import { NM_DIR } from "../paths";

export async function GET() {
  try {
    const nmDir = NM_DIR();
    const files = await readdir(nmDir);
    const reports = files
      .filter((f) => f.startsWith("report-") && f.endsWith(".md"))
      .sort()
      .reverse();

    const summaries = await Promise.all(
      reports.map(async (f) => {
        const fileStat = await stat(join(nmDir, f));
        const id = f.replace(".md", "");
        return {
          id,
          date: fileStat.mtime.toISOString(),
          filename: f,
        };
      }),
    );

    return Response.json(summaries);
  } catch {
    return Response.json([]);
  }
}
