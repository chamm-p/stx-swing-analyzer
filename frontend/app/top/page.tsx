"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { api, runAnalysis } from "@/lib/api";
import SignalBadge from "@/components/SignalBadge";

type ScreenerRow = {
  symbol: string;
  name: string | null;
  segment: string | null;
  action: string;
  technical_score: number;
  close: number | null;
  snapshot: Record<string, any> | null;
};

type TopResponse = { run_at: string | null; running: boolean; results: ScreenerRow[] };

type PortfolioOption = { id: number; name: string; kind: string };

const SEGMENTS = [
  { key: null, label: "Alle" },
  { key: "US", label: "US-Aktien" },
  { key: "DAX", label: "DAX" },
  { key: "CRYPTO", label: "Top Cryptos" },
] as const;

type SortKey = "strength" | "symbol" | "segment" | "action" | "score" | "rsi" | "close";

function sortValue(r: ScreenerRow, key: SortKey): string | number {
  switch (key) {
    case "strength": return Math.abs(r.technical_score);
    case "symbol": return r.symbol;
    case "segment": return r.segment ?? "";
    case "action": return { BUY: 3, SELL: 2, HOLD: 1 }[r.action] ?? 0;
    case "score": return r.technical_score;
    case "rsi": return r.snapshot?.rsi14 ?? -Infinity;
    case "close": return r.close ?? -Infinity;
  }
}

export default function TopSignalsPage() {
  const [data, setData] = useState<TopResponse | null>(null);
  const [segment, setSegment] = useState<string | null>(null);
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: "strength", dir: -1 });
  const [portfolios, setPortfolios] = useState<PortfolioOption[]>([]);
  const [targetPortfolio, setTargetPortfolio] = useState<number | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [rowStatus, setRowStatus] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    const seg = segment ? `&segment=${segment}` : "";
    api.get(`/api/screener/top?limit=30${seg}`).then(setData).catch((e) => setError(e.message));
    api.get("/api/portfolios").then((p: PortfolioOption[]) => {
      setPortfolios(p);
      if (p.length > 0) setTargetPortfolio((cur) => cur ?? p[0].id);
    }).catch(() => {});
  }, [segment]);
  useEffect(load, [load]);

  const sorted = useMemo(() => {
    const rows = [...(data?.results ?? [])];
    rows.sort((a, b) => {
      const va = sortValue(a, sort.key);
      const vb = sortValue(b, sort.key);
      if (typeof va === "string") return sort.dir * va.localeCompare(vb as string);
      return sort.dir * ((va as number) - (vb as number));
    });
    return rows;
  }, [data, sort]);

  function toggleSort(key: SortKey) {
    setSort((s) => s.key === key
      ? { key, dir: -s.dir as 1 | -1 }
      : { key, dir: key === "symbol" || key === "segment" ? 1 : -1 });
  }

  async function runScan() {
    setMsg(null);
    try {
      await api.post("/api/screener/run");
      setMsg("⏳ Scan läuft — Universum wird abgerufen und bewertet…");
      // Status pollen und Liste automatisch aktualisieren, sobald fertig
      const seg = segment ? `&segment=${segment}` : "";
      for (let i = 0; i < 120; i++) {
        await new Promise((r) => setTimeout(r, 5000));
        const d = await api.get(`/api/screener/top?limit=30${seg}`);
        if (!d.running) {
          setData(d);
          setMsg(`✅ Scan abgeschlossen (${new Date(d.run_at).toLocaleTimeString("de-DE")}).`);
          return;
        }
      }
      setMsg("Scan läuft ungewöhnlich lange — Seite später neu laden.");
    } catch (e: any) {
      setMsg(e.message);
    }
  }

  async function analyze(symbol: string) {
    setRowStatus((s) => ({ ...s, [symbol]: "⏳ analysiere…" }));
    try {
      // Analyse setzt Watchlist/Portfolio-Scope voraus — bei Bedarf
      // erst auf die Watchlist nehmen (409 = ist schon drauf, ok).
      try {
        await api.post("/api/watchlist", { symbol });
      } catch (e: any) {
        if (e.status !== 409) throw e;
      }
      const res = await runAnalysis(symbol);
      const s = res.signal;
      setRowStatus((st) => ({
        ...st,
        [symbol]: res.created && s
          ? `✅ ${s.action} (${Math.round(s.confidence * 100)}%)${s.target_price ? ` · Ziel ${s.target_price}` : ""}`
          : "✅ analysiert — unverändert",
      }));
    } catch (e: any) {
      setRowStatus((s) => ({ ...s, [symbol]: `❌ ${e.message}` }));
    }
  }

  async function toWatchlist(symbol: string) {
    setMsg(null);
    try {
      await api.post("/api/watchlist", { symbol });
      setMsg(`${symbol} zur Watchlist hinzugefügt — LLM-Analyse folgt im nächsten Lauf.`);
    } catch (e: any) {
      setMsg(e.message);
    }
  }

  async function toPortfolio(row: ScreenerRow) {
    if (targetPortfolio === null) {
      setMsg("Zuerst ein Portfolio anlegen (Seite „Portfolios“).");
      return;
    }
    const qty = window.prompt(`Stückzahl für ${row.symbol} (Kurs ~${row.close ?? "?"})`, "10");
    if (!qty) return;
    try {
      const res = await api.post(`/api/portfolios/${targetPortfolio}/positions`, {
        symbol: row.symbol,
        quantity: parseFloat(qty.replace(",", ".")),
      });
      setMsg(`${row.symbol} gekauft zu ${res.entry_price} → Portfolio.`);
    } catch (e: any) {
      setMsg(e.message);
    }
  }

  if (error) return <p className="text-rose-400">Fehler: {error}</p>;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-bold">Top-Signale</h1>
        <span className="text-xs text-slate-500">
          Universum-Screener (rein technisch, unabhängig von Watchlist & Portfolio)
        </span>
        <button onClick={runScan}
          className="ml-auto rounded bg-sky-600 px-3 py-1.5 text-sm font-semibold hover:bg-sky-500">
          Scan starten
        </button>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        {SEGMENTS.map((s) => (
          <button
            key={s.label}
            onClick={() => setSegment(s.key)}
            className={`rounded-full border px-3 py-1 text-xs font-semibold ${
              segment === s.key
                ? "border-sky-500 bg-sky-600/20 text-sky-300"
                : "border-slate-700 text-slate-400 hover:border-slate-500"
            }`}
          >
            {s.label}
          </button>
        ))}
        {portfolios.length > 0 && (
          <label className="ml-auto flex items-center gap-1 text-xs text-slate-500"
            title="Legt fest, in welches Portfolio der →Portfolio-Button einer Zeile kauft">
            „→ Portfolio" kauft in:
            <select
              value={targetPortfolio ?? ""}
              onChange={(e) => setTargetPortfolio(Number(e.target.value))}
              className="rounded border border-slate-700 bg-slate-900 px-2 py-1.5 text-xs text-slate-300"
            >
              {portfolios.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name} ({p.kind === "trial" ? "Trial" : p.kind === "auto" ? "Auto" : "Echt"})
                </option>
              ))}
            </select>
          </label>
        )}
      </div>

      {data?.run_at && (
        <p className="text-xs text-slate-500">
          Letzter Scan: {new Date(data.run_at).toLocaleString("de-DE")}
          {data.running && " — neuer Scan läuft…"}
        </p>
      )}
      {msg && <p className="text-sm text-amber-400">{msg}</p>}

      {!data ? (
        <p className="text-slate-500">Lade…</p>
      ) : data.results.length === 0 ? (
        <p className="text-slate-500">
          Noch kein Scan vorhanden — „Scan starten" klicken (der Worker scannt sonst automatisch alle 6h).
        </p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-slate-800">
          <table className="w-full text-sm">
            <thead className="bg-slate-900 text-left text-slate-400">
              <tr>
                <SortHeader label="#" k="strength" sort={sort} onToggle={toggleSort} title="Signalstärke (|Score|)" />
                <SortHeader label="Symbol" k="symbol" sort={sort} onToggle={toggleSort} />
                <SortHeader label="Segment" k="segment" sort={sort} onToggle={toggleSort} />
                <SortHeader label="Signal" k="action" sort={sort} onToggle={toggleSort} />
                <SortHeader label="Tech-Score" k="score" sort={sort} onToggle={toggleSort} title="Signiert: bullish ↔ bearish" />
                <SortHeader label="RSI" k="rsi" sort={sort} onToggle={toggleSort} />
                <SortHeader label="Kurs" k="close" sort={sort} onToggle={toggleSort} />
                <th className="px-3 py-2"></th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((r, i) => (
                <tr key={r.symbol} className="border-t border-slate-800 hover:bg-slate-900/50">
                  <td className="px-3 py-2 text-slate-500">{i + 1}</td>
                  <td className="px-3 py-2">
                    <Link href={`/asset/${r.symbol}`} className="font-semibold text-sky-400 hover:underline">
                      {r.symbol}
                    </Link>
                    <span className="ml-2 text-xs text-slate-500">{r.name}</span>
                  </td>
                  <td className="px-3 py-2 text-slate-400">{r.segment}</td>
                  <td className="px-3 py-2"><SignalBadge action={r.action} /></td>
                  <td className={`px-3 py-2 font-mono ${r.technical_score > 0 ? "text-emerald-400" : r.technical_score < 0 ? "text-rose-400" : "text-slate-400"}`}>
                    {r.technical_score > 0 ? "+" : ""}{r.technical_score.toFixed(2)}
                  </td>
                  <td className="px-3 py-2 text-slate-400">{r.snapshot?.rsi14?.toFixed(0) ?? "—"}</td>
                  <td className="px-3 py-2">{r.close ?? "—"}</td>
                  <td className="px-3 py-2 text-right">
                    {rowStatus[r.symbol] ? (
                      <span className="mr-2 text-xs text-amber-400">{rowStatus[r.symbol]}</span>
                    ) : null}
                    <button onClick={() => analyze(r.symbol)}
                      className="mr-2 rounded border border-slate-700 px-2 py-1 text-xs text-slate-300 hover:border-amber-500"
                      title="Zur Watchlist + sofortige Voll-Analyse (Indikatoren, LLM, Scoring)">
                      ⚡ Analyse
                    </button>
                    <button onClick={() => toWatchlist(r.symbol)}
                      className="mr-2 rounded border border-slate-700 px-2 py-1 text-xs text-slate-300 hover:border-sky-500">
                      → Watchlist
                    </button>
                    <button onClick={() => toPortfolio(r)}
                      className="rounded border border-slate-700 px-2 py-1 text-xs text-slate-300 hover:border-emerald-500">
                      → Portfolio
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function SortHeader({ label, k, sort, onToggle, title }: {
  label: string; k: SortKey;
  sort: { key: SortKey; dir: 1 | -1 };
  onToggle: (k: SortKey) => void;
  title?: string;
}) {
  const active = sort.key === k;
  return (
    <th className="px-3 py-2">
      <button onClick={() => onToggle(k)} title={title}
        className={`flex items-center gap-1 font-semibold hover:text-white ${active ? "text-sky-400" : ""}`}>
        {label}
        <span className="text-[10px]">{active ? (sort.dir === -1 ? "▼" : "▲") : "↕"}</span>
      </button>
    </th>
  );
}
