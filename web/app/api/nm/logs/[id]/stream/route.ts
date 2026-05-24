import { readFile, stat } from "fs/promises";
import { join } from "path";

const LOGS_DIR = join(process.cwd(), "..", ".night-manager", "logs");

export async function GET(
  _request: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  const logPath = join(LOGS_DIR, `${id}.jsonl`);

  try {
    await stat(logPath);
  } catch {
    return new Response("data: []\n\n", {
      headers: {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        Connection: "keep-alive",
      },
    });
  }

  const encoder = new TextEncoder();
  let lastSize = 0;

  const stream = new ReadableStream({
    async start(controller) {
      try {
        const content = await readFile(logPath, "utf-8");
        const lines = content.trim().split("\n").filter(Boolean);
        for (const line of lines) {
          try {
            const parsed = JSON.parse(line);
            controller.enqueue(
              encoder.encode(`data: ${JSON.stringify(parsed)}\n\n`),
            );
          } catch {
            // skip malformed lines
          }
        }
        lastSize = content.length;
      } catch {
        // file may not exist yet
      }

      const interval = setInterval(async () => {
        try {
          const info = await stat(logPath);
          if (info.size > lastSize) {
            const content = await readFile(logPath, "utf-8");
            const newContent = content.slice(lastSize);
            lastSize = content.length;
            const lines = newContent.trim().split("\n").filter(Boolean);
            for (const line of lines) {
              try {
                const parsed = JSON.parse(line);
                controller.enqueue(
                  encoder.encode(`data: ${JSON.stringify(parsed)}\n\n`),
                );
              } catch {
                // skip
              }
            }
          }
        } catch {
          // file removed or inaccessible
        }
      }, 2000);

      // Clean up on abort
      _request.signal.addEventListener("abort", () => {
        clearInterval(interval);
        controller.close();
      });
    },
  });

  return new Response(stream, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
    },
  });
}
