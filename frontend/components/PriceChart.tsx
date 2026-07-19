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

    const light = document.documentElement.dataset.theme === "light";
    const gridColor = light ? "#e2e8f0" : "#1e293b";
    const chart = createChart(containerRef.current, {
      height: 420,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: light ? "#475569" : "#94a3b8",
      },
      grid: {
        vertLines: { color: gridColor },
        horzLines: { color: gridColor },
      },
      timeScale: { borderColor: light ? "#cbd5e1" : "#334155" },
      rightPriceScale: { borderColor: light ? "#cbd5e1" : "#334155" },
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

  // Legende: nur Kurven zeigen, die auch Daten haben
  const legend: { label: string; color: string; dash?: boolean }[] = [];
  if (indicators.sma20?.length) legend.push({ label: "SMA 20", color: "#38bdf8" });
  if (indicators.sma50?.length) legend.push({ label: "SMA 50", color: "#a78bfa" });
  if (indicators.sma200?.length) legend.push({ label: "SMA 200", color: "#f59e0b" });
  if (indicators.bb_upper?.length || indicators.bb_lower?.length)
    legend.push({ label: "Bollinger-Bänder", color: "#64748b", dash: true });

  return (
    <div className="w-full">
      <div className="mb-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-slate-400">
        <span className="flex items-center gap-1">
          <span className="inline-block h-2 w-3 rounded-sm bg-emerald-500" />
          <span className="inline-block h-2 w-3 rounded-sm bg-rose-500" />
          Kerzen (grün steigend / rot fallend)
        </span>
        {legend.map((l) => (
          <span key={l.label} className="flex items-center gap-1.5">
            <span className="inline-block h-0.5 w-5" style={{
              background: l.dash
                ? `repeating-linear-gradient(90deg, ${l.color} 0 3px, transparent 3px 6px)`
                : l.color,
            }} />
            {l.label}
          </span>
        ))}
        <span className="flex items-center gap-1">
          <span className="text-emerald-400">▲</span>/<span className="text-rose-400">▼</span>
          Signal (BUY / SELL)
        </span>
      </div>
      <div ref={containerRef} className="w-full" />
    </div>
  );
}
