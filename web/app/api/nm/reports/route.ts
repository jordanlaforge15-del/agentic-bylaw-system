import { readdir, stat } from "fs/promises";
import { join } from "path";

const NM_DIR = join(process.cwd(), "..", ".night-manager");

export async function GET() {
  try {
    const files = await readdir(NM_DIR);
    const reports = files
      .filter((f) => f.startsWith("report-") && f.endsWith(".md"))
      .sort()
      .reverse();

    const summaries = await Promise.all(
      reports.map(async (f) => {
        const fileStat = await stat(join(NM_DIR, f));
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
