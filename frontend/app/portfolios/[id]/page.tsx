"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { api } from "@/lib/api";
import EquityChart from "@/components/EquityChart";
import SymbolSearch from "@/components/SymbolSearch";

type PositionRow = {
  id: string; symbol: string; quantity: number; entry_price: number; entry_date: string;
  exit_price: number | null; exit_date: string | null; is_open: boolean;
  current_price: number | null; value: number | null; invested: number;
  pnl_abs: number | null; pnl_pct: number | null; notes: string | null;
  source: string; horizon_days: number | null; fees: number;
};

type Detail = {
  summary: {
    id: number; name: string; kind: string; invested: number; value: number;
    pnl_abs: number; pnl_pct: number; realized_pnl: number; open_positions: number;
    platform_id: number | null; platform_name: string | null; fees_total: number;
  };
  positions: PositionRow[];
};

export default function PortfolioDetailPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [detail, setDetail] = useState<Detail | null>(null);
  const [history, setHistory] = useState<{ time: string; value: number }[]>([]);
  const [benchmark, setBenchmark] = useState<{ time: string; value: number }[]>([]);
  const [benchmarkLabel, setBenchmarkLabel] = useState("Benchmark");
  const [symbol, setSymbol] = useState("");
  const [quantity, setQuantity] = useState("");
  const [price, setPrice] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api.get(`/api/portfolios/${id}`).then(setDetail).catch((e) => {
      if (e.status === 404) router.push("/portfolios");
      else setError(e.message);
    });
    api.get(`/api/portfolios/${id}/history`).then((d) => {
      setHistory(d.series || []);
      setBenchmark(d.benchmark || []);
      setBenchmarkLabel(d.benchmark_symbol || "Benchmark");
    }).catch(() => {});
  }, [id, router]);
  useEffect(load, [load]);

  async function addPosition(e: React.FormEvent) {
    e.preventDefault();
    if (!symbol.trim() || !quantity) return;
    setBusy(true);
    setError(null);
    try {
      await api.post(`/api/portfolios/${id}/positions`, {
        symbol: symbol.trim(),
        quantity: parseFloat(quantity.replace(",", ".")),
        entry_price: price ? parseFloat(price.replace(",", ".")) : null,
      });
      setSymbol(""); setQuantity(""); setPrice("");
      load();
    } catch (err: any) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function closePosition(p: PositionRow) {
    const qtyInput = window.prompt(
      `Wie viele Stück von ${p.symbol} verkaufen? (Bestand: ${p.quantity})`,
      String(p.quantity)
    );
    if (qtyInput === null) return;
    const qty = parseFloat(qtyInput.replace(",", "."));
    if (!qty || qty <= 0) return;
    const px = window.prompt(`Verkaufskurs für ${p.symbol} (leer = aktueller Kurs ${p.current_price ?? "?"})`, "");
    if (px === null) return;
    let exitPrice: number | null = px ? parseFloat(px.replace(",", ".")) : null;
    let ibkrNote = "";

    // Echte Order zuerst — der Fill-Preis wird zum Buchungskurs.
    const sm = detail?.summary;
    const viaIbkr = sm?.kind === "real"
      && (sm?.platform_name || "").toUpperCase().startsWith("IBKR")
      && confirm(`🏦 Echte SELL-Order (Market) über IBKR senden: ${qty} × ${p.symbol}?\n\nOK = Order an IBKR + App-Buchung · Abbrechen = nur in der App buchen`);
    if (viaIbkr) {
      try {
        const order = await api.post("/api/broker/ibkr/order", {
          symbol: p.symbol, side: "SELL", quantity: qty,
          order_type: "MKT", confirm: true,
        });
        if (order.avg_fill_price) exitPrice = order.avg_fill_price;
        ibkrNote = ` · IBKR ${order.status}` +
          (order.avg_fill_price ? ` @ ${order.avg_fill_price}` : "") +
          (order.commission ? `, Kommission ${order.commission}` : "");
      } catch (err: any) {
        if (!confirm(`❌ IBKR-Order fehlgeschlagen: ${err.message}\n\nTrotzdem nur in der App buchen?`)) return;
      }
    }

    try {
      const res = await api.post(`/api/positions/${p.id}/close`, {
        quantity: qty,
        exit_price: exitPrice,
      });
      setError(null);
      alert(`${res.sold_quantity} Stück zu ${res.exit_price} verkauft` +
        (res.remaining > 0 ? ` — ${res.remaining} bleiben offen.` : ".") + ibkrNote);
      load();
    } catch (err: any) {
      setError(err.message);
    }
  }

  async function deletePosition(p: PositionRow) {
    if (!confirm(`Position ${p.symbol} (${p.quantity} Stk.) löschen?`)) return;
    await api.del(`/api/positions/${p.id}`);
    load();
  }

  if (error && !detail) return <p className="text-rose-400">Fehler: {error}</p>;
  if (!detail) return <p className="text-slate-500">Lade…</p>;
  const s = detail.summary;

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <Link href="/portfolios" className="text-slate-500 hover:text-white">←</Link>
        <h1 className="text-xl font-bold">{s.name}</h1>
        <span className={`rounded border px-2 py-0.5 text-xs ${s.kind === "trial" ? "border-amber-600 text-amber-400" : "border-emerald-700 text-emerald-400"}`}>
          {s.kind === "trial" ? "TRIAL" : "ECHT"}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-6">
        <Stat label="Aktueller Wert" value={fmtNum(s.value)} />
        <Stat label="Investiert" value={fmtNum(s.invested)} />
        <Stat label="Unrealisiert" value={`${s.pnl_abs >= 0 ? "+" : ""}${fmtNum(s.pnl_abs)} (${s.pnl_pct.toFixed(1)}%)`}
          tone={s.pnl_abs >= 0 ? "pos" : "neg"} />
        <Stat label="Realisiert" value={`${s.realized_pnl >= 0 ? "+" : ""}${fmtNum(s.realized_pnl)}`}
          tone={s.realized_pnl >= 0 ? "pos" : "neg"} />
        <Stat label={`Gebühren${s.platform_name ? ` (${s.platform_name})` : ""}`}
          value={fmtNum(s.fees_total)} tone={s.fees_total > 0 ? "neg" : undefined} />
        <Stat label="Offene Positionen" value={String(s.open_positions)} />
      </div>

      <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-2">
        <EquityChart data={history} benchmark={benchmark} benchmarkLabel={benchmarkLabel} />
        {benchmark.length > 0 && (
          <p className="px-2 pb-1 text-xs text-slate-500">
            — Portfolio · ┄ {benchmarkLabel} (auf Startwert normiert)
          </p>
        )}
      </div>

      <form onSubmit={addPosition} className="flex flex-wrap gap-2">
        <SymbolSearch value={symbol} onChange={setSymbol} placeholder="Symbol oder Name"
          className="w-56 rounded border border-slate-700 bg-slate-900 px-3 py-2 text-sm outline-none focus:border-sky-500" />
        <input value={quantity} onChange={(e) => setQuantity(e.target.value)} placeholder="Stückzahl"
          className="w-32 rounded border border-slate-700 bg-slate-900 px-3 py-2 text-sm outline-none focus:border-sky-500" />
        <input value={price} onChange={(e) => setPrice(e.target.value)} placeholder="Kaufkurs (leer = aktuell)"
          className="w-48 rounded border border-slate-700 bg-slate-900 px-3 py-2 text-sm outline-none focus:border-sky-500" />
        <button disabled={busy}
          className="rounded bg-sky-600 px-4 py-2 text-sm font-semibold hover:bg-sky-500 disabled:opacity-50">
          {busy ? "Prüfe…" : "Position hinzufügen"}
        </button>
      </form>
      {error && <p className="text-sm text-rose-400">{error}</p>}

      <div className="overflow-x-auto rounded-lg border border-slate-800">
        <table className="w-full text-sm">
          <thead className="bg-slate-900 text-left text-slate-400">
            <tr>
              <th className="px-3 py-2">Symbol</th>
              <th className="px-3 py-2">Stück</th>
              <th className="px-3 py-2">Einstieg</th>
              <th className="px-3 py-2">Aktuell/Exit</th>
              <th className="px-3 py-2">Wert</th>
              <th className="px-3 py-2">P/L</th>
              <th className="px-3 py-2">Status</th>
              <th className="px-3 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {detail.positions.map((p) => (
              <tr key={p.id} className={`border-t border-slate-800 ${p.is_open ? "" : "opacity-60"}`}>
                <td className="px-3 py-2">
                  <Link href={`/asset/${p.symbol}`} className="font-semibold text-sky-400 hover:underline">{p.symbol}</Link>
                </td>
                <td className="px-3 py-2">{p.quantity}</td>
                <td className="px-3 py-2 text-slate-400">
                  {p.entry_price.toFixed(2)} <span className="text-xs">({new Date(p.entry_date).toLocaleDateString("de-DE")})</span>
                </td>
                <td className="px-3 py-2">{p.current_price?.toFixed(2) ?? "—"}</td>
                <td className="px-3 py-2">{p.value !== null ? fmtNum(p.value) : "—"}</td>
                <td className={`px-3 py-2 font-mono ${(p.pnl_abs ?? 0) >= 0 ? "text-emerald-400" : "text-rose-400"}`}
                  title={p.fees > 0 ? `inkl. ${p.fees.toFixed(2)} Gebühren` : undefined}>
                  {p.pnl_abs !== null ? `${p.pnl_abs >= 0 ? "+" : ""}${fmtNum(p.pnl_abs)} (${p.pnl_pct}%)` : "—"}
                  {p.fees > 0 && <span className="ml-1 text-xs text-slate-500">*</span>}
                </td>
                <td className="px-3 py-2 text-xs text-slate-400">
                  {p.is_open ? "offen" : `verkauft ${p.exit_date ? new Date(p.exit_date).toLocaleDateString("de-DE") : ""}`}
                  {p.source === "auto" && (
                    <span className="ml-1 rounded border border-sky-700 px-1 text-[10px] text-sky-400" title={p.notes || ""}>
                      AUTO{p.horizon_days ? ` ${p.horizon_days}d` : ""}
                    </span>
                  )}
                </td>
                <td className="px-3 py-2 text-right text-xs">
                  {p.is_open ? (
                    <button onClick={() => closePosition(p)} className="mr-2 text-amber-400 hover:underline">Verkaufen</button>
                  ) : (
                    <button
                      onClick={async () => {
                        try {
                          await api.post(`/api/positions/${p.id}/reopen`, {});
                          load();
                        } catch (e: any) {
                          setError(e.message);
                        }
                      }}
                      title="Verkauf rückgängig machen (Teilverkäufe werden wieder verschmolzen)"
                      className="mr-2 text-sky-400 hover:underline"
                    >
                      ↩ Rückgängig
                    </button>
                  )}
                  <button onClick={() => deletePosition(p)} className="text-rose-400 hover:underline">Löschen</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {detail.positions.length === 0 && (
        <p className="text-slate-500">
          Keine Positionen. Oben hinzufügen — oder auf der Seite <Link href="/top" className="text-sky-400 underline">Top-Signale</Link> picken.
        </p>
      )}
    </div>
  );
}

function fmtNum(v: number) {
  return v.toLocaleString("de-DE", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: "pos" | "neg" }) {
  const color = tone === "pos" ? "text-emerald-400" : tone === "neg" ? "text-rose-400" : "";
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
      <div className={`text-sm font-semibold sm:text-base ${color}`}>{value}</div>
      <div className="text-xs text-slate-500">{label}</div>
    </div>
  );
}
