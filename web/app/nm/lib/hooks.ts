"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import useSWR from "swr";
import type { RunState, LogEvent, ReportSummary } from "./types";

const fetcher = (url: string) => fetch(url).then((r) => r.json());

export function useTick(intervalMs = 1000): number {
  const [now, setNow] = useState(0);
  useEffect(() => {
    setNow(Date.now());
    const id = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}

export function useNmState() {
  const { data, error, isLoading, mutate } = useSWR<RunState | null>(
    "/api/nm/state",
    fetcher,
    { refreshInterval: 2000, revalidateOnFocus: true },
  );
  return { state: data ?? null, error, isLoading, mutate };
}

export function useReports() {
  const { data, error, isLoading } = useSWR<ReportSummary[]>(
    "/api/nm/reports",
    fetcher,
    { refreshInterval: 10000 },
  );
  return { reports: data ?? [], error, isLoading };
}

export function useStreamingLog(
  events: LogEvent[],
  intervalMs = 1800,
  startCount?: number,
): LogEvent[] {
  const initial = startCount ?? events.length;
  const [count, setCount] = useState(initial);
  useEffect(() => {
    if (count >= events.length) return;
    const id = setTimeout(
      () => setCount((c) => Math.min(c + 1, events.length)),
      intervalMs,
    );
    return () => clearTimeout(id);
  }, [count, events.length, intervalMs]);
  return events.slice(0, count);
}

export function useLogStream(issueId: string | null) {
  const [events, setEvents] = useState<LogEvent[]>([]);
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    if (!issueId) return;
    setEvents([]);
    const es = new EventSource(`/api/nm/logs/${issueId}/stream`);
    esRef.current = es;
    es.onmessage = (e) => {
      try {
        const event = JSON.parse(e.data) as LogEvent;
        setEvents((prev) => [...prev, event]);
      } catch {
        // skip malformed
      }
    };
    es.onerror = () => {
      es.close();
    };
    return () => {
      es.close();
    };
  }, [issueId]);

  return events;
}

export function useNmTheme() {
  const [theme, setThemeState] = useState<string>("vanguard");

  useEffect(() => {
    const saved = localStorage.getItem("nm:theme");
    if (saved === "apollo" || saved === "vanguard" || saved === "red_october") {
      setThemeState(saved);
    }
  }, []);

  const setTheme = useCallback((next: string) => {
    setThemeState(next);
    localStorage.setItem("nm:theme", next);
    const el = document.getElementById("nm-root");
    if (el) {
      el.setAttribute("data-nm-theme", next);
      el.style.display = "none";
      void el.offsetHeight;
      el.style.display = "";
    }
  }, []);

  return { theme, setTheme };
}
