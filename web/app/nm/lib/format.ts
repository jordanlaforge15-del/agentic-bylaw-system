export function fmtElapsed(ms: number | null | undefined): string {
  if (ms == null || ms < 0) ms = 0;
  const s = Math.floor(ms / 1000);
  const hh = Math.floor(s / 3600);
  const mm = Math.floor((s % 3600) / 60);
  const ss = s % 60;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(hh)}:${pad(mm)}:${pad(ss)}`;
}

export function fmtMinutes(ms: number | null | undefined): string {
  if (ms == null || ms < 0) return "—";
  const s = Math.floor(ms / 1000);
  const mm = Math.floor(s / 60);
  const ss = s % 60;
  return `${mm}m ${String(ss).padStart(2, "0")}s`;
}

export function fmtClock(ms: number): string {
  const d = new Date(ms);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

export function parseISO(s: string | null | undefined): number | null {
  return s ? new Date(s).getTime() : null;
}
