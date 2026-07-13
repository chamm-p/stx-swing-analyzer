"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { api, WatchlistEntry } from "@/lib/api";
import SignalBadge from "@/components/SignalBadge";

export default function WatchlistPage() {
  const [items, setItems] = useState<WatchlistEntry[]>([]);
  const [symbol, setSymbol] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api.get("/api/watchlist").then(setItems).catch((e) => setError(e.message));
  }, []);
  useEffect(load, [load]);

  async function add(e: React.FormEvent) {
    e.preventDefault();
    if (!symbol.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await api.post("/api/watchlist", { symbol: symbol.trim() });
      setSymbol("");
      load();
    } catch (err: any) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function remove(sym: string) {
    if (!confirm(`${sym} von der Watchlist entfernen?`)) return;
    await api.del(`/api/watchlist/${sym}`);
    load();
  }

  async function toggleAlert(item: WatchlistEntry) {
    await api.patch(`/api/watchlist/${item.symbol}`, { alert_enabled: !item.alert_enabled });
    load();
  }

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-bold">Watchlist</h1>

      <form onSubmit={add} className="flex gap-2">
        <input
          value={symbol}
          onChange={(e) => setSymbol(e.target.value.toUpperCase())}
          placeholder="Symbol (z.B. AAPL, SAP.DE, IWDA.AS)"
          className="w-72 rounded border border-slate-700 bg-slate-900 px-3 py-2 text-sm outline-none focus:border-sky-500"
        />
        <button
          disabled={busy}
          className="rounded bg-sky-600 px-4 py-2 text-sm font-semibold hover:bg-sky-500 disabled:opacity-50"
        >
          {busy ? "Prüfe…" : "Hinzufügen"}
        </button>
      </form>
      {error && <p className="text-sm text-rose-400">{error}</p>}

      <div className="grid gap-3 sm:grid-cols-2">
        {items.map((item) => (
          <div key={item.symbol} className="rounded-lg border border-slate-800 bg-slate-900/50 p-4">
            <div className="flex items-start justify-between">
              <div>
                <Link href={`/asset/${item.symbol}`} className="text-lg font-bold text-sky-400 hover:underline">
                  {item.symbol}
                </Link>
                <div className="text-sm text-slate-400">
                  {item.name} · {item.asset_type.toUpperCase()} {item.currency && `· ${item.currency}`}
                </div>
              </div>
              {item.last_signal && (
                <SignalBadge action={item.last_signal.action} confidence={item.last_signal.confidence} />
              )}
            </div>
            {item.source === "portfolio" && (
              <div className="mt-2 text-xs text-sky-400/80">
                aus Portfolio: {item.portfolios?.join(", ")}
              </div>
            )}
            <div className="mt-3 flex items-center gap-3 text-xs text-slate-400">
              {item.source === "watchlist" ? (
                <>
                  <button
                    onClick={() => toggleAlert(item)}
                    className={`rounded border px-2 py-1 ${item.alert_enabled ? "border-emerald-700 text-emerald-400" : "border-slate-700"}`}
                  >
                    Alerts {item.alert_enabled ? "an" : "aus"}
                  </button>
                  <span>min. Confidence {Math.round(item.min_confidence * 100)}%</span>
                  <button onClick={() => remove(item.symbol)} className="ml-auto text-rose-400 hover:underline">
                    Entfernen
                  </button>
                </>
              ) : (
                <span className="text-slate-500">
                  Automatisch beobachtet — abschaltbar über den „Beobachten"-Schalter am Portfolio
                </span>
              )}
            </div>
          </div>
        ))}
      </div>
      {items.length === 0 && !error && (
        <p className="text-slate-500">Watchlist ist leer — Symbol oben hinzufügen.</p>
      )}
    </div>
  );
}
