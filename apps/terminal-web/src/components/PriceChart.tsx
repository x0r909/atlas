"use client";

import { CandlestickSeries, createChart, type IChartApi, type ISeriesApi } from "lightweight-charts";
import { useEffect, useRef } from "react";

import type { Bar } from "@/lib/api";

interface PriceChartProps {
  bars: Bar[];
  label: string;
}

/**
 * One chart per pair. The chart is decorative for assistive tech: the same numbers are in
 * the table next to it, so the canvas is hidden from the accessibility tree.
 */
export function PriceChart({ bars, label }: PriceChartProps) {
  const container = useRef<HTMLDivElement | null>(null);
  const chart = useRef<IChartApi | null>(null);
  const series = useRef<ISeriesApi<"Candlestick"> | null>(null);

  useEffect(() => {
    if (!container.current) {
      return undefined;
    }
    const created = createChart(container.current, {
      height: 280,
      layout: { background: { color: "#0d1117" }, textColor: "#e6edf3" },
      grid: { vertLines: { color: "#21262d" }, horzLines: { color: "#21262d" } },
      rightPriceScale: { borderColor: "#30363d" },
      timeScale: { borderColor: "#30363d", timeVisible: true },
    });
    chart.current = created;
    series.current = created.addSeries(CandlestickSeries, {
      upColor: "#3fb950",
      downColor: "#ff7b72",
      borderVisible: false,
      wickUpColor: "#3fb950",
      wickDownColor: "#ff7b72",
    });
    const observer = new ResizeObserver(() => {
      if (container.current) {
        created.applyOptions({ width: container.current.clientWidth });
      }
    });
    observer.observe(container.current);
    return () => {
      observer.disconnect();
      created.remove();
      chart.current = null;
      series.current = null;
    };
  }, []);

  useEffect(() => {
    series.current?.setData(
      bars.map((bar) => ({
        time: Math.floor(bar.ts / 1000) as never,
        open: Number(bar.open),
        high: Number(bar.high),
        low: Number(bar.low),
        close: Number(bar.close),
      })),
    );
    chart.current?.timeScale().fitContent();
  }, [bars]);

  return (
    <figure style={{ margin: 0 }}>
      <figcaption className="chart-caption">{label}</figcaption>
      <div ref={container} aria-hidden="true" data-testid="price-chart" />
    </figure>
  );
}
