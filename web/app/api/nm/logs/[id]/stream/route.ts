import { readFile, stat } from "fs/promises";
import { join } from "path";
import { NM_DIR, LOGS_DIR, STATE_PATH } from "../../../paths";

function findLogPath(id: string, stateJson: string | null): string {
  if (stateJson) {
    try {
      const state = JSON.parse(stateJson);
      const issue = state.issues?.[id];
      if (issue?.log_file) {
        const lf = issue.log_file;
        if (lf.startsWith("/")) return lf;
        return join(NM_DIR(), "..", lf);
      }
    } catch {
      // fall through
    }
  }
  return join(LOGS_DIR(), `${id}.jsonl`);
}

function parseStreamEvent(line: string) {
  try {
    const ev = JSON.parse(line);
    if (ev.type === "assistant" && ev.message?.content) {
      for (const block of ev.message.content) {
        if (block.type === "tool_use") {
          const args = block.input ? summarizeInput(block.input) : "";
          return { kind: "tool", name: block.name ?? "?", args };
        }
        if (block.type === "text" && block.text) {
          return { kind: "text", text: block.text };
        }
      }
    }
    if (ev.type === "system" && ev.subtype === "init") {
      return {
        kind: "system",
        text: `session started · model=${ev.model ?? "?"}`,
      };
    }
    return null;
  } catch {
    return null;
  }
}

function summarizeInput(input: Record<string, unknown>): string {
  if (typeof input.file_path === "string") return input.file_path;
  if (typeof input.command === "string") {
    const cmd = input.command as string;
    return cmd.length > 80 ? cmd.slice(0, 77) + "..." : cmd;
  }
  if (typeof input.query === "string") return input.query as string;
  if (typeof input.pattern === "string") return input.pattern as string;
  const keys = Object.keys(input);
  if (keys.length === 0) return "";
  return keys.slice(0, 3).join(", ");
}

export async function GET(
  request: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;

  let stateRaw: string | null = null;
  try {
    stateRaw = await readFile(STATE_PATH(), "utf-8");
  } catch {
    // no state file
  }

  const logPath = findLogPath(id, stateRaw);

  try {
    await stat(logPath);
  } catch {
    return new Response("data: []\n\n", { headers: sseHeaders() });
  }

  const encoder = new TextEncoder();
  let lastSize = 0;

  const stream = new ReadableStream({
    async start(controller) {
      try {
        const content = await readFile(logPath, "utf-8");
        const lines = content.trim().split("\n").filter(Boolean);
        for (const line of lines) {
          const parsed = parseStreamEvent(line);
          if (parsed) {
            controller.enqueue(
              encoder.encode(`data: ${JSON.stringify(parsed)}\n\n`),
            );
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
              const parsed = parseStreamEvent(line);
              if (parsed) {
                controller.enqueue(
                  encoder.encode(`data: ${JSON.stringify(parsed)}\n\n`),
                );
              }
            }
          }
        } catch {
          // file removed or inaccessible
        }
      }, 2000);

      request.signal.addEventListener("abort", () => {
        clearInterval(interval);
        controller.close();
      });
    },
  });

  return new Response(stream, { headers: sseHeaders() });
}

function sseHeaders() {
  return {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache",
    Connection: "keep-alive",
  };
}
