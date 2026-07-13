"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { api, runAnalysis, WatchlistEntry } from "@/lib/api";
import SignalBadge from "@/components/SignalBadge";
import SymbolSearch from "@/components/SymbolSearch";

type Entry = WatchlistEntry & { last_close: number | null; last_news_at: string | null };

export default function WatchlistPage() {
  const [items, setItems] = useState<Entry[]>([]);
  const [symbol, setSymbol] = useState("");
  const [busy, setBusy] = useState(false);
  const [rowStatus, setRowStatus] = useState<Record<string, string>>({});
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

  // Schnell-Austragen: bewusst ohne Dialog — Wiederhinzufügen ist ein Einzeiler.
  async function remove(sym: string) {
    setItems((list) => list.filter((i) => i.symbol !== sym));
    try {
      await api.del(`/api/watchlist/${sym}`);
    } catch (e: any) {
      setError(e.message);
      load();
    }
  }

  async function toggleAlert(item: Entry) {
    await api.patch(`/api/watchlist/${item.symbol}`, { alert_enabled: !item.alert_enabled });
    load();
  }

  async function analyze(sym: string) {
    setRowStatus((s) => ({ ...s, [sym]: "⏳" }));
    try {
      const res = await runAnalysis(sym);
      const s = res.signal;
      setRowStatus((st) => ({
        ...st,
        [sym]: res.created && s
          ? `✅ ${s.action} (${Math.round(s.confidence * 100)}%)`
          : "✅ unverändert",
      }));
      load();
    } catch (e: any) {
      setRowStatus((s) => ({ ...s, [sym]: `❌ ${e.message}` }));
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-bold">Watchlist</h1>
        <span className="text-xs text-slate-500">
          Manuelle Einträge + Werte aus beobachteten Portfolios
        </span>
      </div>

      <form onSubmit={add} className="flex gap-2">
        <SymbolSearch value={symbol} onChange={setSymbol}
          placeholder="Symbol oder Name (z.B. Celsius, SAP.DE, BTC-USD)" />
        <button
          disabled={busy}
          className="rounded bg-sky-600 px-4 py-2 text-sm font-semibold hover:bg-sky-500 disabled:opacity-50"
        >
          {busy ? "Prüfe…" : "Hinzufügen"}
        </button>
      </form>
      {error && <p className="text-sm text-rose-400">{error}</p>}

      {items.length === 0 && !error ? (
        <p className="text-slate-500">Watchlist ist leer — Symbol oben hinzufügen.</p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-slate-800">
          <table className="w-full text-sm">
            <thead className="bg-slate-900 text-left text-slate-400">
              <tr>
                <th className="px-3 py-2">Symbol</th>
                <th className="px-3 py-2">Kurs</th>
                <th className="px-3 py-2">Signal</th>
                <th className="px-3 py-2">Ziel / Stop</th>
                <th className="px-3 py-2">News</th>
                <th className="px-3 py-2">Quelle</th>
                <th className="px-3 py-2">Alerts</th>
                <th className="px-3 py-2"></th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => {
                const s = item.last_signal;
                return (
                  <tr key={item.symbol} className="border-t border-slate-800 hover:bg-slate-900/50">
                    <td className="px-3 py-2">
                      <Link href={`/asset/${item.symbol}`} className="font-semibold text-sky-400 hover:underline">
                        {item.symbol}
                      </Link>
                      <span className="ml-2 text-xs text-slate-500">
                        {item.name}{item.asset_type !== "stock" && ` · ${item.asset_type.toUpperCase()}`}
                      </span>
                    </td>
                    <td className="px-3 py-2">
                      {item.last_close != null ? item.last_close.toFixed(2) : "—"}
                      {item.currency && <span className="ml-1 text-xs text-slate-500">{item.currency}</span>}
                    </td>
                    <td className="px-3 py-2">
                      {s ? <SignalBadge action={s.action} confidence={s.confidence} /> : <span className="text-slate-500">—</span>}
                    </td>
                    <td className="px-3 py-2 text-slate-400">
                      {s?.target_price ? (
                        <>
                          <span className="text-emerald-400">{s.target_price.toFixed(2)}</span>
                          {" / "}
                          <span className="text-rose-400">{s.stop_price?.toFixed(2)}</span>
                        </>
                      ) : "—"}
                    </td>
                    <td className="px-3 py-2 text-xs text-slate-500">
                      {item.last_news_at ? new Date(item.last_news_at).toLocaleDateString("de-DE") : "—"}
                    </td>
                    <td className="px-3 py-2 text-xs">
                      {item.source === "portfolio" ? (
                        <span className="text-sky-400/80" title={`aus Portfolio: ${item.portfolios?.join(", ")}`}>
                          📁 {item.portfolios?.join(", ")}
                        </span>
                      ) : (
                        <span className="text-slate-500">Watchlist</span>
                      )}
                    </td>
                    <td className="px-3 py-2">
                      {item.source === "watchlist" ? (
                        <button
                          onClick={() => toggleAlert(item)}
                          className={`rounded border px-2 py-0.5 text-xs ${item.alert_enabled ? "border-emerald-700 text-emerald-400" : "border-slate-700 text-slate-500"}`}
                        >
                          {item.alert_enabled ? "an" : "aus"}
                        </button>
                      ) : (
                        <span className="text-xs text-slate-600" title="Alerts über den Beobachten-Schalter am Portfolio">an*</span>
                      )}
                    </td>
                    <td className="px-3 py-2 text-right">
                      {rowStatus[item.symbol] && (
                        <span className="mr-2 text-xs text-amber-400">{rowStatus[item.symbol]}</span>
                      )}
                      <button onClick={() => analyze(item.symbol)}
                        className="mr-2 rounded border border-slate-700 px-2 py-1 text-xs text-slate-300 hover:border-amber-500"
                        title="Sofort analysieren">
                        ⚡
                      </button>
                      {item.source === "watchlist" ? (
                        <button onClick={() => remove(item.symbol)}
                          className="rounded border border-slate-700 px-2 py-1 text-xs text-rose-400 hover:border-rose-500"
                          title="Von der Watchlist entfernen">
                          ✕
                        </button>
                      ) : (
                        <span className="inline-block w-7" title="Über den Beobachten-Schalter am Portfolio steuerbar" />
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <p className="text-xs text-slate-600">
        📁 = automatisch aus beobachtetem Portfolio · an* = Alerts folgen dem Portfolio-Schalter (Default-Confidence 50%)
      </p>
    </div>
  );
}
