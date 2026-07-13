"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api, Signal } from "@/lib/api";
import SignalBadge from "@/components/SignalBadge";

type Dashboard = {
  watchlist_count: number;
  news_last_24h: number;
  recent_signals: Signal[];
};

export default function DashboardPage() {
  const [data, setData] = useState<Dashboard | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.get("/api/dashboard").then(setData).catch((e) => setError(e.message));
  }, []);

  if (error) return <p className="text-rose-400">Fehler: {error}</p>;
  if (!data) return <p className="text-slate-500">Lade…</p>;

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
        <Stat label="Watchlist-Assets" value={data.watchlist_count} />
        <Stat label="News (24h)" value={data.news_last_24h} />
        <Stat label="Signale gesamt" value={data.recent_signals.length} />
      </div>

      <section>
        <h2 className="mb-3 text-lg font-semibold">Aktuelle Signale</h2>
        {data.recent_signals.length === 0 ? (
          <p className="text-slate-500">
            Noch keine Signale. Assets zur <Link href="/watchlist" className="text-sky-400 underline">Watchlist</Link>{" "}
            hinzufügen — der Worker analysiert sie automatisch.
          </p>
        ) : (
          <div className="overflow-x-auto rounded-lg border border-slate-800">
            <table className="w-full text-sm">
              <thead className="bg-slate-900 text-left text-slate-400">
                <tr>
                  <th className="px-3 py-2">Zeit</th>
                  <th className="px-3 py-2">Symbol</th>
                  <th className="px-3 py-2">Signal</th>
                  <th className="px-3 py-2">Kurs</th>
                  <th className="px-3 py-2">Scores (T/S/F)</th>
                  <th className="px-3 py-2">Horizont</th>
                </tr>
              </thead>
              <tbody>
                {data.recent_signals.map((s) => (
                  <tr key={s.id} className="border-t border-slate-800 hover:bg-slate-900/50">
                    <td className="px-3 py-2 text-slate-400">{new Date(s.ts).toLocaleString("de-DE")}</td>
                    <td className="px-3 py-2">
                      <Link href={`/asset/${s.symbol}`} className="font-semibold text-sky-400 hover:underline">
                        {s.symbol}
                      </Link>
                    </td>
                    <td className="px-3 py-2"><SignalBadge action={s.action} confidence={s.confidence} /></td>
                    <td className="px-3 py-2">{s.price_at_signal ?? "—"}</td>
                    <td className="px-3 py-2 text-slate-400">
                      {fmt(s.technical_score)} / {fmt(s.sentiment_score)} / {fmt(s.fundamental_score)}
                    </td>
                    <td className="px-3 py-2 text-slate-400">~{s.horizon_days}d</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

function fmt(v: number | null) {
  return v === null ? "—" : (v >= 0 ? "+" : "") + v.toFixed(2);
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-4">
      <div className="text-2xl font-bold">{value}</div>
      <div className="text-sm text-slate-400">{label}</div>
    </div>
  );
}
