"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import EquityChart from "@/components/EquityChart";

type RunSummary = {
  id: string; created_at: string; status: string; label: string | null;
  segment: string; days: number; params: Record<string, any>; error: string | null;
  total_return_pct: number | null; benchmark_return_pct: number | null;
  sharpe: number | null; max_drawdown_pct: number | null; num_trades: number | null;
  win_rate: number | null; profit_factor: number | null; fees_total: number | null;
};

type RunDetail = RunSummary & {
  metrics: Record<string, any> | null;
  equity: { time: string; value: number }[];
  benchmark: { time: string; value: number }[];
  trades: any[];
  warnings: string[];
};

const PARAM_FIELDS: { key: string; label: string; def: string }[] = [
  { key: "threshold", label: "Schwelle", def: "0.35" },
  { key: "position_size", label: "Positionsgröße", def: "1000" },
  { key: "max_positions", label: "Max. Positionen", def: "10" },
  { key: "target_atr_factor", label: "Ziel ×ATR", def: "2.0" },
  { key: "stop_atr_factor", label: "Stop ×ATR", def: "1.5" },
  { key: "horizon_days", label: "Horizont (Tage)", def: "14" },
  { key: "slippage_bps", label: "Slippage (bps)", def: "5" },
  { key: "start_capital", label: "Startkapital", def: "10000" },
];

export default function BacktestPage() {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [platforms, setPlatforms] = useState<{ id: number; name: string }[]>([]);
  const [segment, setSegment] = useState("US");
  const [days, setDays] = useState("730");
  const [backfill, setBackfill] = useState(false);
  const [platformId, setPlatformId] = useState<number | "">("");
  const [label, setLabel] = useState("");
  const [paramValues, setParamValues] = useState<Record<string, string>>(
    Object.fromEntries(PARAM_FIELDS.map((f) => [f.key, f.def]))
  );
  const [msg, setMsg] = useState<string | null>(null);

  const load = useCallback(() => {
    api.get("/api/backtest/runs").then(setRuns).catch(() => {});
    api.get("/api/platforms").then((p) => {
      setPlatforms(p);
      setPlatformId((cur) => (cur === "" && p.length > 0 ? p[0].id : cur));
    }).catch(() => {});
  }, []);
  useEffect(load, [load]);

  async function start() {
    setMsg(null);
    const params: Record<string, number> = {};
    for (const f of PARAM_FIELDS) {
      const v = parseFloat((paramValues[f.key] || "").replace(",", "."));
      if (!isNaN(v)) params[f.key] = v;
    }
    try {
      const res = await api.post("/api/backtest/run", {
        label: label.trim() || null,
        segment: segment === "alle" ? null : segment,
        days: parseInt(days),
        backfill,
        platform_id: platformId === "" ? null : platformId,
        params,
      });
      setMsg(backfill
        ? "⏳ Lauf gestartet — Backfill der Historie kann einige Minuten dauern…"
        : "⏳ Lauf gestartet…");
      pollRun(res.id);
    } catch (e: any) {
      setMsg(`❌ ${e.message}`);
    }
  }

  async function pollRun(id: string) {
    for (let i = 0; i < 240; i++) {
      await new Promise((r) => setTimeout(r, 3000));
      try {
        const d = await api.get(`/api/backtest/runs/${id}`);
        if (d.status === "done") {
          setMsg(`✅ Lauf abgeschlossen: ${d.total_return_pct}% (Benchmark ${d.benchmark_return_pct ?? "—"}%)`);
          load();
          setDetail(d);
          return;
        }
        if (d.status === "error") {
          setMsg(`❌ Lauf fehlgeschlagen: ${d.error}`);
          load();
          return;
        }
      } catch {}
    }
    setMsg("Lauf dauert ungewöhnlich lange — Liste später aktualisieren.");
  }

  async function openDetail(id: string) {
    setDetail(await api.get(`/api/backtest/runs/${id}`));
  }

  async function remove(id: string) {
    await api.del(`/api/backtest/runs/${id}`);
    if (detail?.id === id) setDetail(null);
    load();
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-bold">Backtest</h1>
        <span className="text-xs text-slate-500">
          Rein technische Strategie-Simulation (Signal auf Close, Fill am Folge-Open,
          Stop vor Ziel, inkl. Gebühren) — LLM-Anteile sind rückwirkend nicht testbar
        </span>
      </div>

      <section className="rounded-lg border border-slate-800 bg-slate-900/50 p-4">
        <div className="flex flex-wrap gap-3">
          <Field label="Segment">
            <select value={segment} onChange={(e) => setSegment(e.target.value)} className={inputCls}>
              <option value="US">US-Aktien</option>
              <option value="DAX">DAX</option>
              <option value="CRYPTO">Cryptos</option>
              <option value="alle">Gesamtes Universum</option>
            </select>
          </Field>
          <Field label="Zeitraum">
            <select value={days} onChange={(e) => setDays(e.target.value)} className={inputCls}>
              <option value="730">2 Jahre</option>
              <option value="1825">5 Jahre</option>
              <option value="3650">10 Jahre</option>
            </select>
          </Field>
          <Field label="Gebühren">
            <select value={platformId} onChange={(e) => setPlatformId(e.target.value === "" ? "" : Number(e.target.value))} className={inputCls}>
              <option value="">keine</option>
              {platforms.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </Field>
          <label className="flex items-end gap-1 pb-2 text-xs text-slate-400"
            title="Fehlende ältere Kurshistorie einmalig von Yahoo nachladen (dauert einige Minuten)">
            <input type="checkbox" checked={backfill} onChange={(e) => setBackfill(e.target.checked)} />
            Historie nachladen
          </label>
          <Field label="Label (optional)">
            <input value={label} onChange={(e) => setLabel(e.target.value)}
              placeholder="z.B. Schwelle 0.40" className={inputCls + " w-44"} />
          </Field>
        </div>
        <div className="mt-2 flex flex-wrap gap-3">
          {PARAM_FIELDS.map((f) => (
            <Field key={f.key} label={f.label}>
              <input value={paramValues[f.key]}
                onChange={(e) => setParamValues({ ...paramValues, [f.key]: e.target.value })}
                className={inputCls + " w-28"} />
            </Field>
          ))}
        </div>
        <div className="mt-3 flex items-center gap-3">
          <button onClick={start} className="rounded bg-sky-600 px-4 py-2 text-sm font-semibold hover:bg-sky-500">
            Backtest starten
          </button>
          {msg && <span className="text-sm text-amber-400">{msg}</span>}
        </div>
      </section>

      {detail && (
        <section className="rounded-lg border border-sky-900/60 bg-slate-900/50 p-4">
          <div className="mb-2 flex items-center gap-3">
            <h2 className="font-semibold">
              Lauf {detail.label || detail.id.slice(0, 8)} — {detail.segment}, {Math.round(detail.days / 365)}J
            </h2>
            <button onClick={() => setDetail(null)} className="ml-auto text-xs text-slate-500 hover:text-white">✕ schließen</button>
          </div>
          <div className="mb-3 grid grid-cols-3 gap-2 sm:grid-cols-6">
            <Stat label="Rendite" value={`${detail.total_return_pct}%`}
              tone={(detail.total_return_pct ?? 0) >= 0 ? "pos" : "neg"} />
            <Stat label="Benchmark (SPY)" value={detail.benchmark_return_pct != null ? `${detail.benchmark_return_pct}%` : "—"} />
            <Stat label="Sharpe" value={String(detail.sharpe ?? "—")} />
            <Stat label="Max Drawdown" value={`${detail.max_drawdown_pct ?? "—"}%`} tone="neg" />
            <Stat label="Trades / WinRate" value={`${detail.num_trades} / ${detail.win_rate != null ? Math.round(detail.win_rate * 100) + "%" : "—"}`} />
            <Stat label="Gebühren" value={String(detail.fees_total ?? 0)} />
          </div>
          <EquityChart data={detail.equity} benchmark={detail.benchmark} benchmarkLabel="SPY" />
          {detail.metrics?.exit_reasons && (
            <p className="mt-2 text-xs text-slate-500">
              Exits: 🎯 Ziel {detail.metrics.exit_reasons.target} · 🛑 Stop {detail.metrics.exit_reasons.stop} ·
              ⏱ Horizont {detail.metrics.exit_reasons.horizon} · 📉 Signal {detail.metrics.exit_reasons.signal}
              {" · "}Ø Gewinn {detail.metrics.avg_win} / Ø Verlust {detail.metrics.avg_loss}
            </p>
          )}
          {detail.trades.length > 0 && (
            <details className="mt-2">
              <summary className="cursor-pointer text-xs text-slate-400">Trades ({detail.trades.length})</summary>
              <div className="mt-1 max-h-64 overflow-y-auto">
                <table className="w-full text-xs">
                  <thead className="text-left text-slate-500">
                    <tr><th>Symbol</th><th>Einstieg</th><th>Exit</th><th>Grund</th><th className="text-right">P/L</th></tr>
                  </thead>
                  <tbody>
                    {detail.trades.map((t, i) => (
                      <tr key={i} className="border-t border-slate-800/60">
                        <td className="py-0.5">{t.symbol}</td>
                        <td>{t.entry_date} @ {t.entry_price}</td>
                        <td>{t.exit_date ? `${t.exit_date} @ ${t.exit_price}` : "offen"}</td>
                        <td>{t.reason || "—"}</td>
                        <td className={`text-right font-mono ${(t.pnl ?? 0) >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                          {t.pnl ?? "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </details>
          )}
        </section>
      )}

      <section>
        <h2 className="mb-2 font-semibold">Läufe</h2>
        {runs.length === 0 ? (
          <p className="text-sm text-slate-500">Noch keine Backtests — oben den ersten Lauf starten.</p>
        ) : (
          <div className="overflow-x-auto rounded-lg border border-slate-800">
            <table className="w-full text-sm">
              <thead className="bg-slate-900 text-left text-slate-400">
                <tr>
                  <th className="px-3 py-2">Zeit</th>
                  <th className="px-3 py-2">Label</th>
                  <th className="px-3 py-2">Segment</th>
                  <th className="px-3 py-2">Rendite</th>
                  <th className="px-3 py-2">SPY</th>
                  <th className="px-3 py-2">Sharpe</th>
                  <th className="px-3 py-2">MaxDD</th>
                  <th className="px-3 py-2">Trades</th>
                  <th className="px-3 py-2">PF</th>
                  <th className="px-3 py-2"></th>
                </tr>
              </thead>
              <tbody>
                {runs.map((r) => (
                  <tr key={r.id} className="cursor-pointer border-t border-slate-800 hover:bg-slate-900/50"
                    onClick={() => r.status === "done" && openDetail(r.id)}>
                    <td className="px-3 py-2 text-xs text-slate-400">{new Date(r.created_at).toLocaleString("de-DE")}</td>
                    <td className="px-3 py-2">{r.label || <span className="text-slate-600">—</span>}
                      {r.status === "running" && <span className="ml-1 text-xs text-amber-400">⏳</span>}
                      {r.status === "error" && <span className="ml-1 text-xs text-rose-400" title={r.error || ""}>❌</span>}
                    </td>
                    <td className="px-3 py-2 text-slate-400">{r.segment} · {Math.round(r.days / 365)}J</td>
                    <td className={`px-3 py-2 font-mono ${(r.total_return_pct ?? 0) >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                      {r.total_return_pct != null ? `${r.total_return_pct}%` : "—"}
                    </td>
                    <td className="px-3 py-2 text-slate-400">{r.benchmark_return_pct != null ? `${r.benchmark_return_pct}%` : "—"}</td>
                    <td className="px-3 py-2">{r.sharpe ?? "—"}</td>
                    <td className="px-3 py-2 text-slate-400">{r.max_drawdown_pct != null ? `${r.max_drawdown_pct}%` : "—"}</td>
                    <td className="px-3 py-2">{r.num_trades ?? "—"}{r.win_rate != null && <span className="text-xs text-slate-500"> ({Math.round(r.win_rate * 100)}%)</span>}</td>
                    <td className="px-3 py-2">{r.profit_factor ?? "—"}</td>
                    <td className="px-3 py-2 text-right">
                      <button onClick={(e) => { e.stopPropagation(); remove(r.id); }}
                        className="text-xs text-rose-400 hover:underline">✕</button>
                    </td>
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

const inputCls = "rounded border border-slate-700 bg-slate-900 px-2 py-1.5 text-sm outline-none focus:border-sky-500";

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1 text-xs text-slate-400">
      {label}
      {children}
    </label>
  );
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: "pos" | "neg" }) {
  const color = tone === "pos" ? "text-emerald-400" : tone === "neg" ? "text-rose-400" : "";
  return (
    <div className="rounded border border-slate-800 bg-slate-900/40 p-2">
      <div className={`text-sm font-semibold ${color}`}>{value}</div>
      <div className="text-xs text-slate-500">{label}</div>
    </div>
  );
}
