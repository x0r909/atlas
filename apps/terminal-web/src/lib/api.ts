import type { CSSProperties } from "react";

/** The generated types are the same ones the gateway emits; regenerating them must not break here. */
export type Mode = "PAPER" | "CO_PILOT" | "AUTONOMOUS" | "HALTED" | "BACKTEST";

export interface Position {
  position_id: string;
  venue_id: string;
  pair: string;
  status: string;
  quantity: string;
  entry_price: string;
  stop_price: string;
  target_price: string | null;
  opened_at_ms: number;
  stop_confirmation: "CONFIRMED" | "UNCONFIRMED";
  unrealised_pnl_idr: string | null;
}

export interface Bar {
  ts: number;
  open: string;
  high: string;
  low: string;
  close: string;
  volume: string;
}

export interface NewsItem {
  id: string;
  source_id: string;
  source_name: string;
  title: string;
  url: string;
  language: "id" | "en";
  published_at_ms: number | null;
  fetched_at_ms: number;
  tickers: string[];
  /** Rule ids from the injection screen; a non-empty list means the agent never sees it. */
  injection_flags: string[];
  excluded_from_prompts: boolean;
  duplicate_of: string | null;
}

export const API_BASE = process.env.NEXT_PUBLIC_ATLAS_API ?? "";

export class ApiError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    credentials: "include",
    headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as {
      error?: { code?: string; message?: string };
    } | null;
    throw new ApiError(
      body?.error?.code ?? `HTTP_${response.status}`,
      body?.error?.message ?? response.statusText,
      response.status,
    );
  }
  return (await response.json()) as T;
}

export const fetchPositions = (signal?: AbortSignal) =>
  call<{ positions: Position[] }>("/positions?status=open", { signal });
export const fetchMode = (signal?: AbortSignal) =>
  call<{ mode: Mode; current: { since_ms: number; reason_code: string }; history: unknown[] }>(
    "/mode",
    { signal },
  );
export const fetchNews = (sinceMs: number, signal?: AbortSignal) =>
  call<{ items: NewsItem[] }>(`/news?since=${sinceMs}`, { signal });

export const fetchBars = (pair: string, tf: string, fromMs: number, signal?: AbortSignal) =>
  call<{ bars: Bar[]; truncated: boolean; age_ms: number | null }>(
    `/market/bars?pair=${pair}&tf=${tf}&from=${fromMs}`,
    { signal },
  );

/** The kill switch needs no confirmation by design; it must never wait for a modal. */
export function killSwitch(): Promise<{ mode: Mode }> {
  return call<{ mode: Mode }>("/killswitch", { method: "POST", body: "{}" });
}

/** One colour per mode, in one place: a chain of ternaries hid BACKTEST inside PAPER. */
const MODE_BACKGROUND: Record<Mode, string> = {
  HALTED: "#ff7b72",
  AUTONOMOUS: "#3fb950",
  CO_PILOT: "#d29922",
  PAPER: "#8b949e",
  BACKTEST: "#8b949e",
};

export const badgeStyle = (mode: Mode): CSSProperties => ({
  padding: "0.15rem 0.6rem",
  borderRadius: "999px",
  fontWeight: 600,
  color: "#0d1117",
  background: MODE_BACKGROUND[mode],
});
