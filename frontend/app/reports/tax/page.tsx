"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";

type Trade = {
  portfolio: string; symbol: string; currency: string | null; quantity: number;
  entry_date: string | null; entry_price: number; exit_date: string;
  exit_price: number; proceeds: number; cost: number; fees: number; pnl: number;
};
type Holding = {
  portfolio: string; symbol: string; currency: string | null; quantity: number;
  entry_price: number; year_end_close: number | null; year_end_value: number | null;
};
type Report = {
  year: number; portfolios: string[]; trades: Trade[]; holdings: Holding[];
  totals: { realized_pnl: number; fees: number; num_trades: number; holdings_value: number };
  notes: string[];
};

function toCsv(rows: Record<string, any>[]): string {
  if (rows.length === 0) return "";
  const cols = Object.keys(rows[0]);
  const esc = (v: any) => `"${String(v ?? "").replace(/"/g, '""')}"`;
  return [cols.join(";"), ...rows.map((r) => cols.map((c) => esc(r[c])).join(";"))].join("\n");
}

function download(filename: string, content: string) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob(["﻿" + content], { type: "text/csv;charset=utf-8" }));
  a.download = filename;
  a.click();
}

export default function TaxReportPage() {
  const currentYear = new Date().getFullYear();
  const [year, setYear] = useState(currentYear - 1);
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback((y: number) => {
    setError(null);
    api.get(`/api/reports/tax/${y}`).then(setReport).catch((e) => {
      setReport(null);
      setError(e.message);
    });
  }, []);
  useEffect(() => load(year), [year, load]);

  const fmt = (v: number | null | undefined) =>
    v == null ? "—" : v.toLocaleString("de-CH", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  return (
    <div className="space-y-4 print:text-black">
      <div className="flex flex-wrap items-center gap-3 print:hidden">
        <h1 className="text-xl font-bold">🧾 Steuerreport (Aktienhandel)</h1>
        <select value={year} onChange={(e) => setYear(Number(e.target.value))}
          className="rounded border border-slate-700 bg-slate-900 px-2 py-1 text-sm">
          {Array.from({ length: 6 }, (_, i) => currentYear - i).map((y) => (
            <option key={y} value={y}>{y}</option>
          ))}
        </select>
        <button onClick={() => window.print()}
          className="rounded border border-slate-700 px-3 py-1 text-sm text-slate-300 hover:border-sky-500">
          🖨️ Drucken / PDF
        </button>
        {report && (
          <>
            <button onClick={() => download(`trades-${year}.csv`, toCsv(report.trades))}
              className="rounded border border-slate-700 px-3 py-1 text-sm text-slate-300 hover:border-sky-500">
              ⬇ Trades CSV
            </button>
            <button onClick={() => download(`bestand-${year}.csv`, toCsv(report.holdings))}
              className="rounded border border-slate-700 px-3 py-1 text-sm text-slate-300 hover:border-sky-500">
              ⬇ Bestand CSV
            </button>
          </>
        )}
      </div>

      {error && <p className="text-rose-400">{error}</p>}
      {!report && !error && <p className="text-slate-500">Lade…</p>}

      {report && (
        <>
          <div className="hidden print:block">
            <h1 className="text-xl font-bold">Steuerreport Aktienhandel {report.year}</h1>
            <p className="text-sm">Depots: {report.portfolios.join(", ")}</p>
          </div>

          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            {[
              ["Realisierter G/V", fmt(report.totals.realized_pnl)],
              ["Gebühren gesamt", fmt(report.totals.fees)],
              ["Anzahl Trades", String(report.totals.num_trades)],
              ["Bestand per 31.12.", fmt(report.totals.holdings_value)],
            ].map(([label, value]) => (
              <div key={label} className="rounded-lg border border-slate-800 bg-slate-900/50 p-3 print:border-black">
                <div className="text-lg font-bold">{value}</div>
                <div className="text-xs text-slate-400 print:text-black">{label}</div>
              </div>
            ))}
          </div>

          <section>
            <h2 className="mb-2 font-semibold">Realisierte Trades {report.year} ({report.trades.length})</h2>
            <div className="overflow-x-auto rounded border border-slate-800 print:border-black">
              <table className="w-full text-xs">
                <thead className="bg-slate-900 text-left text-slate-400 print:text-black">
                  <tr>
                    {["Depot", "Symbol", "Whg", "Stück", "Kauf", "Kaufkurs", "Verkauf", "Verkaufskurs", "Erlös", "Einstand", "Gebühren", "G/V"].map((h) => (
                      <th key={h} className="px-2 py-1">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {report.trades.map((t, i) => (
                    <tr key={i} className="border-t border-slate-800 print:border-gray-400">
                      <td className="px-2 py-1">{t.portfolio}</td>
                      <td className="px-2 py-1 font-semibold">{t.symbol}</td>
                      <td className="px-2 py-1">{t.currency ?? ""}</td>
                      <td className="px-2 py-1 text-right">{t.quantity}</td>
                      <td className="px-2 py-1">{t.entry_date}</td>
                      <td className="px-2 py-1 text-right">{fmt(t.entry_price)}</td>
                      <td className="px-2 py-1">{t.exit_date}</td>
                      <td className="px-2 py-1 text-right">{fmt(t.exit_price)}</td>
                      <td className="px-2 py-1 text-right">{fmt(t.proceeds)}</td>
                      <td className="px-2 py-1 text-right">{fmt(t.cost)}</td>
                      <td className="px-2 py-1 text-right">{fmt(t.fees)}</td>
                      <td className={`px-2 py-1 text-right font-semibold ${t.pnl >= 0 ? "text-emerald-400" : "text-rose-400"} print:text-black`}>
                        {fmt(t.pnl)}
                      </td>
                    </tr>
                  ))}
                  {report.trades.length === 0 && (
                    <tr><td colSpan={12} className="px-2 py-2 text-slate-500">Keine realisierten Trades in {report.year}.</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </section>

          <section>
            <h2 className="mb-2 font-semibold">Wertschriftenverzeichnis per 31.12.{report.year} ({report.holdings.length})</h2>
            <div className="overflow-x-auto rounded border border-slate-800 print:border-black">
              <table className="w-full text-xs">
                <thead className="bg-slate-900 text-left text-slate-400 print:text-black">
                  <tr>
                    {["Depot", "Symbol", "Whg", "Stück", "Einstandskurs", "Schlusskurs 31.12.", "Wert 31.12."].map((h) => (
                      <th key={h} className="px-2 py-1">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {report.holdings.map((h, i) => (
                    <tr key={i} className="border-t border-slate-800 print:border-gray-400">
                      <td className="px-2 py-1">{h.portfolio}</td>
                      <td className="px-2 py-1 font-semibold">{h.symbol}</td>
                      <td className="px-2 py-1">{h.currency ?? ""}</td>
                      <td className="px-2 py-1 text-right">{h.quantity}</td>
                      <td className="px-2 py-1 text-right">{fmt(h.entry_price)}</td>
                      <td className="px-2 py-1 text-right">{fmt(h.year_end_close)}</td>
                      <td className="px-2 py-1 text-right font-semibold">{fmt(h.year_end_value)}</td>
                    </tr>
                  ))}
                  {report.holdings.length === 0 && (
                    <tr><td colSpan={7} className="px-2 py-2 text-slate-500">Kein Bestand per 31.12.{report.year}.</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </section>

          <div className="text-xs text-slate-500 print:text-black">
            {report.notes.map((n) => <p key={n}>ℹ️ {n}</p>)}
          </div>
        </>
      )}
    </div>
  );
}
