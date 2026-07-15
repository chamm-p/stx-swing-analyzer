"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";

const ITEMS = [
  { href: "/review", label: "Review", hint: "Signal-Trefferquote & Kalibrierung" },
  { href: "/backtest", label: "Backtest", hint: "Strategie testen & optimieren" },
];

export default function NavOptimierung() {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const pathname = usePathname();
  const active = ITEMS.some((i) => pathname.startsWith(i.href));

  useEffect(() => {
    function onClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, []);
  useEffect(() => setOpen(false), [pathname]);

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen((o) => !o)}
        className={`flex items-center gap-1 text-sm hover:text-white ${active ? "text-sky-400" : "text-slate-300"}`}
      >
        Optimierung
        <span className="text-[9px]">▼</span>
      </button>
      {open && (
        <div className="absolute left-0 top-full z-50 mt-1 min-w-44 rounded-lg border border-slate-700 bg-slate-900 py-1 shadow-xl">
          {ITEMS.map((i) => (
            <Link key={i.href} href={i.href}
              className={`block px-3 py-1.5 text-sm hover:bg-slate-800 ${pathname.startsWith(i.href) ? "text-sky-400" : "text-slate-300"}`}>
              {i.label}
              <span className="block text-[11px] text-slate-500">{i.hint}</span>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
