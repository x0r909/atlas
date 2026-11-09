"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, fetchNews, type NewsItem } from "@/lib/api";

const REFRESH_MS = 60_000;
const WINDOW_HOURS = 24;

function timeAgo(ts: number, now: number): string {
  const minutes = Math.max(0, Math.round((now - ts) / 60_000));
  if (minutes < 60) {
    return `${minutes}m ago`;
  }
  return `${Math.round(minutes / 60)}h ago`;
}

/**
 * Headlines only. The body text is deliberately never rendered as page content: an item the
 * injection screen flagged is shown, marked, and linked, never interpreted here.
 */
export default function NewsPage() {
  const [items, setItems] = useState<NewsItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const inFlight = useRef<AbortController | null>(null);

  const refresh = useCallback(async () => {
    inFlight.current?.abort();
    const controller = new AbortController();
    inFlight.current = controller;
    setNow(Date.now());
    try {
      const since = Date.now() - WINDOW_HOURS * 3_600_000;
      const body = await fetchNews(since, controller.signal);
      setItems(body.items);
      setError(null);
    } catch (cause) {
      if (controller.signal.aborted) {
        return;
      }
      setError(cause instanceof ApiError ? `${cause.code}: ${cause.message}` : String(cause));
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = setInterval(() => {
      if (!document.hidden) {
        void refresh();
      }
    }, REFRESH_MS);
    const onVisible = () => {
      if (!document.hidden) {
        void refresh();
      }
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
      inFlight.current?.abort();
    };
  }, [refresh]);

  return (
    <main>
      <h1>News</h1>
      <p className="muted">Last {WINDOW_HOURS} hours, one row per story. Flags come from the screen, not from the model.</p>
      {error !== null && (
        <p className="alert" role="alert">
          {error}
        </p>
      )}
      {items.length === 0 && error === null && <p className="muted">No items in the window.</p>}
      <table>
        <caption className="muted">Headlines; excluded items are listed but never enter a prompt.</caption>
        <thead>
          <tr>
            <th scope="col">Age</th>
            <th scope="col">Source</th>
            <th scope="col">Headline</th>
            <th scope="col">Tickers</th>
            <th scope="col">Screen</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.id}>
              <td className="num">{timeAgo(item.published_at_ms ?? item.fetched_at_ms, now)}</td>
              <td>
                {item.source_name} <span className="muted">({item.language})</span>
              </td>
              <td>
                <a href={item.url} rel="noopener noreferrer" target="_blank">
                  {item.title}
                </a>
                {item.duplicate_of !== null && <span className="muted"> · duplicate</span>}
              </td>
              <td>{item.tickers.length > 0 ? item.tickers.join(", ") : "—"}</td>
              <td>
                {item.excluded_from_prompts ? (
                  <span className="warning" title={item.injection_flags.join(", ")}>
                    EXCLUDED
                  </span>
                ) : (
                  <span className="ok">clean</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </main>
  );
}
