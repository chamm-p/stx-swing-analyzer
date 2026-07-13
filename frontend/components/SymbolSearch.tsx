"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";

type Hit = { symbol: string; name: string | null; exchange: string | null; type: string | null };

/** Symbol-Eingabe mit Klarnamen-Suche (Yahoo): "Celsius" → CELH. */
export default function SymbolSearch({ value, onChange, placeholder, className }: {
  value: string;
  onChange: (symbol: string) => void;
  placeholder?: string;
  className?: string;
}) {
  const [hits, setHits] = useState<Hit[]>([]);
  const [open, setOpen] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const close = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  function handleInput(raw: string) {
    onChange(raw.toUpperCase());
    if (timer.current) clearTimeout(timer.current);
    const q = raw.trim();
    if (q.length < 2) {
      setHits([]);
      setOpen(false);
      return;
    }
    timer.current = setTimeout(async () => {
      try {
        const res = await api.get(`/api/search?q=${encodeURIComponent(q)}`);
        setHits(res);
        setOpen(res.length > 0);
      } catch {
        setHits([]);
      }
    }, 300);
  }

  return (
    <div ref={boxRef} className="relative">
      <input
        value={value}
        onChange={(e) => handleInput(e.target.value)}
        onFocus={() => hits.length > 0 && setOpen(true)}
        placeholder={placeholder || "Symbol oder Name (z.B. Celsius, SAP)"}
        className={className || "w-72 rounded border border-slate-700 bg-slate-900 px-3 py-2 text-sm outline-none focus:border-sky-500"}
      />
      {open && (
        <div className="absolute z-40 mt-1 w-full min-w-72 overflow-hidden rounded border border-slate-700 bg-slate-900 shadow-xl">
          {hits.map((h) => (
            <button
              key={h.symbol}
              type="button"
              onClick={() => {
                onChange(h.symbol);
                setOpen(false);
              }}
              className="flex w-full items-baseline gap-2 px-3 py-1.5 text-left text-sm hover:bg-slate-800"
            >
              <span className="font-semibold text-sky-400">{h.symbol}</span>
              <span className="truncate text-xs text-slate-400">{h.name}</span>
              <span className="ml-auto shrink-0 text-xs text-slate-600">{h.exchange}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
