"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";

type PortfolioSummary = {
  id: number; name: string; kind: string; open_positions: number;
  invested: number; value: number; pnl_abs: number; pnl_pct: number; realized_pnl: number;
};

export default function PortfoliosPage() {
  const [portfolios, setPortfolios] = useState<PortfolioSummary[]>([]);
  const [name, setName] = useState("");
  const [kind, setKind] = useState<"real" | "trial">("real");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api.get("/api/portfolios").then(setPortfolios).catch((e) => setError(e.message));
  }, []);
  useEffect(load, [load]);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    try {
      await api.post("/api/portfolios", { name: name.trim(), kind });
      setName("");
      load();
    } catch (err: any) {
      setError(err.message);
    }
  }

  async function remove(p: PortfolioSummary) {
    if (!confirm(`Portfolio "${p.name}" inkl. aller Positionen löschen?`)) return;
    await api.del(`/api/portfolios/${p.id}`);
    load();
  }

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-bold">Portfolios</h1>

      <form onSubmit={create} className="flex flex-wrap gap-2">
        <input value={name} onChange={(e) => setName(e.target.value)}
          placeholder='Name (z.B. "Depot comdirect", "Swing-Test Q3")'
          className="w-72 rounded border border-slate-700 bg-slate-900 px-3 py-2 text-sm outline-none focus:border-sky-500" />
        <select value={kind} onChange={(e) => setKind(e.target.value as "real" | "trial")}
          className="rounded border border-slate-700 bg-slate-900 px-3 py-2 text-sm">
          <option value="real">Echtes Portfolio</option>
          <option value="trial">Trial (Strategie-Test)</option>
        </select>
        <button className="rounded bg-sky-600 px-4 py-2 text-sm font-semibold hover:bg-sky-500">Anlegen</button>
      </form>
      {error && <p className="text-sm text-rose-400">{error}</p>}

      <div className="grid gap-4 sm:grid-cols-2">
        {portfolios.map((p) => (
          <div key={p.id} className="rounded-lg border border-slate-800 bg-slate-900/50 p-4">
            <div className="flex items-start justify-between">
              <Link href={`/portfolios/${p.id}`} className="text-lg font-bold text-sky-400 hover:underline">
                {p.name}
              </Link>
              <span className={`rounded border px-2 py-0.5 text-xs ${p.kind === "trial" ? "border-amber-600 text-amber-400" : "border-emerald-700 text-emerald-400"}`}>
                {p.kind === "trial" ? "TRIAL" : "ECHT"}
              </span>
            </div>
            <div className="mt-3 grid grid-cols-3 gap-2 text-sm">
              <div>
                <div className="font-semibold">{p.value.toLocaleString("de-DE", { maximumFractionDigits: 0 })}</div>
                <div className="text-xs text-slate-500">Wert</div>
              </div>
              <div>
                <div className={`font-semibold ${p.pnl_abs >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                  {p.pnl_abs >= 0 ? "+" : ""}{p.pnl_abs.toLocaleString("de-DE", { maximumFractionDigits: 0 })} ({p.pnl_pct.toFixed(1)}%)
                </div>
                <div className="text-xs text-slate-500">Unrealisiert</div>
              </div>
              <div>
                <div className="font-semibold">{p.open_positions}</div>
                <div className="text-xs text-slate-500">Positionen</div>
              </div>
            </div>
            <div className="mt-3 flex items-center text-xs text-slate-500">
              {p.realized_pnl !== 0 && (
                <span>Realisiert: {p.realized_pnl >= 0 ? "+" : ""}{p.realized_pnl.toLocaleString("de-DE")}</span>
              )}
              <button onClick={() => remove(p)} className="ml-auto text-rose-400 hover:underline">Löschen</button>
            </div>
          </div>
        ))}
      </div>
      {portfolios.length === 0 && !error && (
        <p className="text-slate-500">
          Noch keine Portfolios. Tipp: ein „echtes" für laufende Positionen und ein „Trial" zum Strategie-Testen anlegen.
        </p>
      )}
    </div>
  );
}
