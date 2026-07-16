import { readdir, readFile, stat } from "fs/promises";
import { join } from "path";
import { NextRequest } from "next/server";
import { NM_DIR } from "../paths";

export async function GET(req: NextRequest) {
  const id = req.nextUrl.searchParams.get("id");

  if (id) {
    if (!/^report-[\d-]+$/.test(id))
      return new Response("invalid id", { status: 400 });
    try {
      const content = await readFile(join(NM_DIR(), `${id}.md`), "utf-8");
      return new Response(content, {
        headers: { "Content-Type": "text/plain; charset=utf-8" },
      });
    } catch {
      return new Response("report not found", { status: 404 });
    }
  }

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
