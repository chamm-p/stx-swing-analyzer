"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import SignalBadge from "@/components/SignalBadge";

type Group = {
  action: string;
  asset_type: string;
  count: number;
  hit_rate: number | null;
  avg_return_pct: number;
};

type Summary = { evaluated_count: number; has_pending: boolean; groups: Group[] };

type EvaluatedSignal = {
  id: string; symbol: string; ts: string; action: string; confidence: number;
  price_at_signal: number | null; horizon_days: number; eval_price: number | null;
  eval_return_pct: number | null; eval_hit: boolean | null; evaluated_at: string;
};

export default function ReviewPage() {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [signals, setSignals] = useState<EvaluatedSignal[]>([]);
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api.get("/api/review/summary").then(setSummary).catch((e) => setError(e.message));
    api.get("/api/review/signals?limit=50").then(setSignals).catch(() => {});
  }, []);
  useEffect(load, [load]);

  async function runNow() {
    setMsg(null);
    try {
      const res = await api.post("/api/review/run");
      setMsg(`${res.evaluated} Signale ausgewertet.`);
      load();
    } catch (e: any) {
      setMsg(e.message);
    }
  }

  if (error) return <p className="text-rose-400">Fehler: {error}</p>;

  const buyGroups = summary?.groups.filter((g) => g.action === "BUY") ?? [];
  const totalHitRate = (() => {
    const withHits = summary?.groups.filter((g) => g.hit_rate !== null) ?? [];
    const total = withHits.reduce((a, g) => a + g.count, 0);
    if (!total) return null;
    return withHits.reduce((a, g) => a + (g.hit_rate ?? 0) * g.count, 0) / total;
  })();

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <h1 className="text-xl font-bold">Signal-Review</h1>
        <span className="text-xs text-slate-500">
          Jedes Signal wird nach Ablauf seines Horizonts gegen die tatsächliche Kursentwicklung ausgewertet
        </span>
        <button onClick={runNow}
          className="ml-auto rounded bg-sky-600 px-3 py-1.5 text-sm font-semibold hover:bg-sky-500">
          Jetzt auswerten
        </button>
      </div>
      {msg && <p className="text-sm text-amber-400">{msg}</p>}

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        <Stat label="Ausgewertete Signale" value={String(summary?.evaluated_count ?? "—")} />
        <Stat label="Trefferquote (BUY/SELL)" value={totalHitRate !== null ? `${(totalHitRate * 100).toFixed(0)}%` : "—"}
          tone={totalHitRate !== null ? (totalHitRate >= 0.5 ? "pos" : "neg") : undefined} />
        <Stat label="Ø Rendite BUY" value={buyGroups.length
          ? `${(buyGroups.reduce((a, g) => a + g.avg_return_pct * g.count, 0) / buyGroups.reduce((a, g) => a + g.count, 0)).toFixed(2)}%`
          : "—"} />
      </div>

      {summary && summary.groups.length > 0 ? (
        <div className="overflow-x-auto rounded-lg border border-slate-800">
          <table className="w-full text-sm">
            <thead className="bg-slate-900 text-left text-slate-400">
              <tr>
                <th className="px-3 py-2">Aktion</th>
                <th className="px-3 py-2">Asset-Klasse</th>
                <th className="px-3 py-2">Anzahl</th>
                <th className="px-3 py-2">Trefferquote</th>
                <th className="px-3 py-2">Ø Rendite n. Horizont</th>
              </tr>
            </thead>
            <tbody>
              {summary.groups.map((g) => (
                <tr key={`${g.action}-${g.asset_type}`} className="border-t border-slate-800">
                  <td className="px-3 py-2"><SignalBadge action={g.action} /></td>
                  <td className="px-3 py-2 text-slate-400">{g.asset_type}</td>
                  <td className="px-3 py-2">{g.count}</td>
                  <td className="px-3 py-2">{g.hit_rate !== null ? `${(g.hit_rate * 100).toFixed(0)}%` : "—"}</td>
                  <td className={`px-3 py-2 font-mono ${g.avg_return_pct >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                    {g.avg_return_pct >= 0 ? "+" : ""}{g.avg_return_pct.toFixed(2)}%
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="text-slate-500">
          Noch keine ausgewerteten Signale — Signale werden bewertet, sobald ihr Horizont (3–30 Tage) abgelaufen ist.
          {summary?.has_pending && " Es warten Signale auf ihre Fälligkeit."}
        </p>
      )}

      <p className="rounded border border-slate-800 bg-slate-900/40 p-3 text-xs text-slate-500">
        Hinweis: Belastbare Aussagen brauchen ausreichend Stichprobe — Trefferquoten aus wenigen Signalen
        sind Rauschen. Parameter-Änderungen sollten über Backtesting validiert werden, nicht direkt aus
        dieser Tabelle abgeleitet.
      </p>

      {signals.length > 0 && (
        <section>
          <h2 className="mb-2 font-semibold">Zuletzt ausgewertet</h2>
          <div className="overflow-x-auto rounded-lg border border-slate-800">
            <table className="w-full text-sm">
              <thead className="bg-slate-900 text-left text-slate-400">
                <tr>
                  <th className="px-3 py-2">Signal</th>
                  <th className="px-3 py-2">Symbol</th>
                  <th className="px-3 py-2">Datum</th>
                  <th className="px-3 py-2">Kurs → n. Horizont</th>
                  <th className="px-3 py-2">Rendite</th>
                  <th className="px-3 py-2">Treffer</th>
                </tr>
              </thead>
              <tbody>
                {signals.map((s) => (
                  <tr key={s.id} className="border-t border-slate-800">
                    <td className="px-3 py-2"><SignalBadge action={s.action} confidence={s.confidence} /></td>
                    <td className="px-3 py-2">
                      <Link href={`/asset/${s.symbol}`} className="font-semibold text-sky-400 hover:underline">{s.symbol}</Link>
                    </td>
                    <td className="px-3 py-2 text-slate-400">
                      {new Date(s.ts).toLocaleDateString("de-DE")} (+{s.horizon_days}d)
                    </td>
                    <td className="px-3 py-2 text-slate-400">
                      {s.price_at_signal?.toFixed(2)} → {s.eval_price?.toFixed(2)}
                    </td>
                    <td className={`px-3 py-2 font-mono ${(s.eval_return_pct ?? 0) >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                      {(s.eval_return_pct ?? 0) >= 0 ? "+" : ""}{s.eval_return_pct?.toFixed(2)}%
                    </td>
                    <td className="px-3 py-2">
                      {s.eval_hit === null ? "—" : s.eval_hit ? "✅" : "❌"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: "pos" | "neg" }) {
  const color = tone === "pos" ? "text-emerald-400" : tone === "neg" ? "text-rose-400" : "";
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-4">
      <div className={`text-2xl font-bold ${color}`}>{value}</div>
      <div className="text-sm text-slate-400">{label}</div>
    </div>
  );
}
