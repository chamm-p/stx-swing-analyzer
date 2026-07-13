"use client";

import { useEffect, useRef } from "react";
import { createChart, ColorType, Time } from "lightweight-charts";

export default function EquityChart({ data }: { data: { time: string; value: number }[] }) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!containerRef.current || data.length === 0) return;
    const light = document.documentElement.dataset.theme === "light";
    const gridColor = light ? "#e2e8f0" : "#1e293b";
    const chart = createChart(containerRef.current, {
      height: 260,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: light ? "#475569" : "#94a3b8",
      },
      grid: { vertLines: { color: gridColor }, horzLines: { color: gridColor } },
      timeScale: { borderColor: light ? "#cbd5e1" : "#334155" },
      rightPriceScale: { borderColor: light ? "#cbd5e1" : "#334155" },
    });
    const first = data[0].value;
    const last = data[data.length - 1].value;
    const color = last >= first ? "#10b981" : "#f43f5e";
    const series = chart.addAreaSeries({
      lineColor: color,
      topColor: color + "33",
      bottomColor: "transparent",
      lineWidth: 2,
    });
    series.setData(data as { time: Time; value: number }[]);
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
  }, [data]);

  if (data.length === 0) {
    return <div className="flex h-[260px] items-center justify-center text-slate-500">Noch keine Historie</div>;
  }
  return <div ref={containerRef} className="w-full" />;
}
