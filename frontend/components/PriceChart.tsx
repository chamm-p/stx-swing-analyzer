"use client";

import { useEffect, useRef } from "react";
import {
  createChart, ColorType, CandlestickSeriesPartialOptions, Time, SeriesMarker,
} from "lightweight-charts";
import type { Signal } from "@/lib/api";

type Candle = { time: string; open: number; high: number; low: number; close: number };
type Point = { time: string; value: number };

export default function PriceChart({
  candles, indicators, signals,
}: {
  candles: Candle[];
  indicators: Record<string, Point[]>;
  signals: Signal[];
}) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!containerRef.current || candles.length === 0) return;

    const chart = createChart(containerRef.current, {
      height: 420,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: "#94a3b8",
      },
      grid: {
        vertLines: { color: "#1e293b" },
        horzLines: { color: "#1e293b" },
      },
      timeScale: { borderColor: "#334155" },
      rightPriceScale: { borderColor: "#334155" },
    });

    const candleSeries = chart.addCandlestickSeries({
      upColor: "#10b981", downColor: "#f43f5e",
      wickUpColor: "#10b981", wickDownColor: "#f43f5e",
      borderVisible: false,
    } as CandlestickSeriesPartialOptions);
    candleSeries.setData(candles as { time: Time; open: number; high: number; low: number; close: number }[]);

    const lineColors: Record<string, string> = {
      sma20: "#38bdf8", sma50: "#a78bfa", sma200: "#f59e0b",
      bb_upper: "#475569", bb_lower: "#475569",
    };
    for (const [name, color] of Object.entries(lineColors)) {
      const data = indicators[name];
      if (data && data.length > 0) {
        const line = chart.addLineSeries({ color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
        line.setData(data as { time: Time; value: number }[]);
      }
    }

    // Signal-Markierungen auf dem Chart
    const markers: SeriesMarker<Time>[] = signals
      .filter((s) => s.action !== "HOLD")
      .map((s) => ({
        time: s.ts.slice(0, 10) as Time,
        position: s.action === "BUY" ? "belowBar" : "aboveBar",
        color: s.action === "BUY" ? "#10b981" : "#f43f5e",
        shape: s.action === "BUY" ? "arrowUp" : "arrowDown",
        text: `${s.action} ${Math.round(s.confidence * 100)}%`,
      }));
    markers.sort((a, b) => String(a.time).localeCompare(String(b.time)));
    candleSeries.setMarkers(markers);

    chart.timeScale().fitContent();

    const onResize = () => {
      if (containerRef.current) chart.applyOptions({ width: containerRef.current.clientWidth });
    };
    onResize();
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      chart.remove();
    };
  }, [candles, indicators, signals]);

  if (candles.length === 0) {
    return <div className="flex h-[420px] items-center justify-center text-slate-500">Noch keine Kursdaten</div>;
  }
  return <div ref={containerRef} className="w-full" />;
}
