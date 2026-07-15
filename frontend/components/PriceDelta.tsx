"use client";

import { useEffect, useState } from "react";

// Gemeinsame Phase (0 = Vortag, 1 = 7 Tage), damit ALLE Deltas auf allen
// Listen synchron im 4-Sekunden-Takt umschalten (ein Timer für die ganze App).
let _phase = 0;
const _subs = new Set<(p: number) => void>();
let _timer: ReturnType<typeof setInterval> | null = null;

function _ensureTimer() {
  if (_timer) return;
  _timer = setInterval(() => {
    _phase = _phase ? 0 : 1;
    _subs.forEach((f) => f(_phase));
  }, 4000);
}

function useAltPhase(): number {
  const [p, setP] = useState(_phase);
  useEffect(() => {
    _subs.add(setP);
    _ensureTimer();
    setP(_phase);
    return () => {
      _subs.delete(setP);
      if (_subs.size === 0 && _timer) {
        clearInterval(_timer);
        _timer = null;
      }
    };
  }, []);
  return p;
}

/**
 * Kursdifferenz-Badge: wechselt alle 4 s zwischen Vortag (1T) und 7 Tagen (7T).
 * Positiv grün (+), negativ rot (−). `null` = keine Daten (—).
 */
export default function PriceDelta({
  d1,
  d7,
  className = "",
}: {
  d1: number | null | undefined;
  d7: number | null | undefined;
  className?: string;
}) {
  const showSeven = useAltPhase() === 1;
  const val = showSeven ? d7 : d1;
  const label = showSeven ? "7T" : "1T";

  // Feste Mindestbreite, damit die Tabelle beim 1T/7T-Wechsel nicht
  // springt (Werte sind unterschiedlich breit: "+0.5%" vs. "−11.15%").
  const base = "inline-block min-w-[4.75rem] whitespace-nowrap tabular-nums ";

  if (val == null) {
    return <span className={base + "text-slate-600 " + className}>—</span>;
  }
  const up = val >= 0;
  return (
    <span
      className={
        base +
        (up ? "text-emerald-400" : "text-rose-400") +
        " transition-colors " +
        className
      }
      title={showSeven ? "Kursänderung 7 Tage" : "Kursänderung Vortag"}
    >
      {up ? "+" : ""}
      {val.toFixed(2)}%
      <span className="ml-1 text-[10px] text-slate-500">{label}</span>
    </span>
  );
}
