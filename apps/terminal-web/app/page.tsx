"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { PriceChart } from "@/components/PriceChart";
import {
  ApiError,
  badgeStyle,
  fetchBars,
  fetchMode,
  fetchPositions,
  killSwitch,
  type Bar,
  type Mode,
  type Position,
} from "@/lib/api";

const PAIR = "BTCIDR";
const TIMEFRAME = "15m";
const HOURS_OF_BARS = 24 * 7;

function formatIdr(value: string | null): string {
  if (value === null) {
    return "—";
  }
  return new Intl.NumberFormat("id-ID", { maximumFractionDigits: 0 }).format(Number(value));
}

function formatPrice(value: string): string {
  return new Intl.NumberFormat("id-ID", { maximumFractionDigits: 0 }).format(Number(value));
}

export default function DashboardPage() {
  const [mode, setMode] = useState<Mode | null>(null);
  const [positions, setPositions] = useState<Position[]>([]);
  const [bars, setBars] = useState<Bar[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [halting, setHalting] = useState(false);

  // A 15 s timer plus a slow gateway used to overlap requests and let an older answer win.
  // Every refresh takes a ticket; only the newest may write state, and the previous one is
  // aborted so a hung request cannot keep a stale panel alive.
  const ticket = useRef(0);

  const refresh = useCallback(async (signal?: AbortSignal) => {
    const mine = ++ticket.current;
    try {
      const [modeBody, positionsBody, barsBody] = await Promise.all([
        fetchMode(signal),
        fetchPositions(signal),
        fetchBars(PAIR, TIMEFRAME, Date.now() - HOURS_OF_BARS * 3600_000, signal),
      ]);
      if (mine !== ticket.current) {
        return;
      }
      setMode(modeBody.mode);
      setPositions(positionsBody.positions);
      setBars(barsBody.bars);
      setError(null);
    } catch (cause) {
      if (cause instanceof DOMException && cause.name === "AbortError") {
        return;
      }
      if (mine !== ticket.current) {
        return;
      }
      setError(
        cause instanceof ApiError ? `${cause.code}: ${cause.message}` : "gateway unreachable",
      );
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    const poll = () => {
      // Nothing to show on a hidden tab, and the terminal should not hammer the gateway.
      if (document.hidden) {
        return;
      }
      void refresh(controller.signal);
    };
    void refresh(controller.signal);
    const timer = setInterval(poll, 15_000);
    document.addEventListener("visibilitychange", poll);
    return () => {
      controller.abort();
      clearInterval(timer);
      document.removeEventListener("visibilitychange", poll);
    };
  }, [refresh]);

  const onKill = async () => {
    setHalting(true);
    try {
      await killSwitch();
      await refresh();
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "kill switch failed");
    } finally {
      setHalting(false);
    }
  };

  const unconfirmed = useMemo(
    () => positions.filter((position) => position.stop_confirmation === "UNCONFIRMED").length,
    [positions],
  );

  return (
    <main>
      <header className="toolbar">
        <h1>ATLAS Terminal</h1>
        <span aria-live="polite" style={mode ? badgeStyle(mode) : undefined}>
          {mode ?? "…"}
        </span>
        <button
          type="button"
          className="kill"
          onClick={() => void onKill()}
          disabled={halting || mode === "HALTED"}
        >
          {mode === "HALTED" ? "Halted" : halting ? "Halting…" : "Kill switch"}
        </button>
      </header>

      {error ? (
        <p role="alert" className="alert">
          {error}
        </p>
      ) : null}
      {unconfirmed > 0 ? (
        <p role="alert" className="alert">
          {unconfirmed} position(s) without a confirmed exchange-side stop
        </p>
      ) : null}

      <section aria-labelledby="positions-heading">
        <h2 id="positions-heading">Open positions</h2>
        <table>
          <caption>Live positions, newest first. No position is hidden or truncated.</caption>
          <thead>
            <tr>
              <th scope="col">Pair</th>
              <th scope="col">Venue</th>
              <th scope="col">Quantity</th>
              <th scope="col">Entry</th>
              <th scope="col">Stop</th>
              <th scope="col">Target</th>
              <th scope="col">Stop protection</th>
              <th scope="col">Unrealised</th>
            </tr>
          </thead>
          <tbody>
            {positions.length === 0 ? (
              <tr>
                <td colSpan={8}>No open positions.</td>
              </tr>
            ) : (
              positions.map((position) => (
                <tr key={position.position_id}>
                  <th scope="row">{position.pair}</th>
                  <td>{position.venue_id}</td>
                  <td>{position.quantity}</td>
                  <td>{formatPrice(position.entry_price)}</td>
                  <td>{formatPrice(position.stop_price)}</td>
                  <td>{position.target_price ? formatPrice(position.target_price) : "—"}</td>
                  <td
                    className={
                      position.stop_confirmation === "CONFIRMED" ? "ok" : "warning"
                    }
                  >
                    {position.stop_confirmation}
                  </td>
                  <td>{formatIdr(position.unrealised_pnl_idr)}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </section>

      <section aria-labelledby="chart-heading">
        <h2 id="chart-heading">
          {PAIR} · {TIMEFRAME}
        </h2>
        <PriceChart bars={bars} label={`${PAIR} ${TIMEFRAME} candles, last 7 days`} />
        <table>
          <caption>Same candles as the chart, as text.</caption>
          <thead>
            <tr>
              <th scope="col">Time</th>
              <th scope="col">Open</th>
              <th scope="col">High</th>
              <th scope="col">Low</th>
              <th scope="col">Close</th>
            </tr>
          </thead>
          <tbody>
            {bars.slice(-10).map((bar) => (
              <tr key={bar.ts}>
                <th scope="row">{new Date(bar.ts).toISOString()}</th>
                <td>{formatPrice(bar.open)}</td>
                <td>{formatPrice(bar.high)}</td>
                <td>{formatPrice(bar.low)}</td>
                <td>{formatPrice(bar.close)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </main>
  );
}
