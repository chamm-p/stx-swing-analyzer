"use client";

import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";

type PortfolioOpt = {
  id: number; name: string; kind: string;
  platform_id: number | null; platform_name: string | null;
  cash?: number; start_capital?: number;
};
type Platform = { id: number; name: string; fees: Record<string, { up_to: number | null; fee: number }[]> };

/** Gebühren-Vorschau (Client-Spiegel der Server-Staffel — der Server
 *  bucht beim Kauf autoritativ). */
function previewFee(platform: Platform | undefined, currency: string | null, volume: number): number {
  if (!platform || volume <= 0) return 0;
  const tiers = platform.fees[currency || ""] || platform.fees["default"] || [];
  const ordered = [...tiers].sort((a, b) =>
    (a.up_to === null ? 1 : 0) - (b.up_to === null ? 1 : 0) || (a.up_to ?? 0) - (b.up_to ?? 0));
  for (const t of ordered) {
    if (t.up_to === null || volume <= t.up_to) return t.fee;
  }
  return 0;
}

export default function BuyDialog({ symbol, defaultPortfolioId, targetPrice, stopPrice, onClose, onBought }: {
  symbol: string;
  defaultPortfolioId?: number | null;
  targetPrice?: number | null;
  stopPrice?: number | null;
  onClose: () => void;
  onBought: (msg: string) => void;
}) {
  const [quote, setQuote] = useState<{ name: string | null; currency: string | null; close: number | null } | null>(null);
  const [portfolios, setPortfolios] = useState<PortfolioOpt[]>([]);
  const [platforms, setPlatforms] = useState<Platform[]>([]);
  const [portfolioId, setPortfolioId] = useState<number | null>(defaultPortfolioId ?? null);
  const [mode, setMode] = useState<"qty" | "budget">("budget");
  const [qtyInput, setQtyInput] = useState("");
  const [budgetInput, setBudgetInput] = useState("1000");
  const [priceInput, setPriceInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.get(`/api/assets/${symbol}/quote`).then(setQuote).catch(() => {});
    api.get("/api/portfolios").then((p: PortfolioOpt[]) => {
      setPortfolios(p);
      setPortfolioId((cur) => cur ?? (p.length > 0 ? p[0].id : null));
    }).catch(() => {});
    api.get("/api/platforms").then(setPlatforms).catch(() => {});
  }, [symbol]);

  const portfolio = portfolios.find((p) => p.id === portfolioId);
  const platform = platforms.find((pl) => pl.id === portfolio?.platform_id);
  const price = parseFloat(priceInput.replace(",", ".")) || quote?.close || 0;

  const quantity = useMemo(() => {
    if (mode === "qty") return parseFloat(qtyInput.replace(",", ".")) || 0;
    const budget = parseFloat(budgetInput.replace(",", ".")) || 0;
    if (!price) return 0;
    const fee = previewFee(platform, quote?.currency ?? null, budget);
    return Math.max(Math.floor(((budget - fee) / price) * 10000) / 10000, 0);
  }, [mode, qtyInput, budgetInput, price, platform, quote]);

  const volume = quantity * price;
  const fee = previewFee(platform, quote?.currency ?? null, volume);
  const total = volume + fee;
  const cashAfter = portfolio?.cash !== undefined ? portfolio.cash - total : undefined;

  async function buy() {
    if (!portfolioId || quantity <= 0) return;
    setBusy(true);
    setError(null);
    try {
      const res = await api.post(`/api/portfolios/${portfolioId}/positions`, {
        symbol,
        quantity,
        entry_price: priceInput.trim() ? price : null,
      });
      onBought(`✅ ${quantity} × ${symbol} gekauft zu ${res.entry_price}` +
        (res.fee ? ` (+ ${res.fee} Gebühr)` : "") + ` → ${portfolio?.name}`);
      onClose();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  const fmt = (v: number) => v.toLocaleString("de-DE", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
      <div className="w-full max-w-md rounded-lg border border-slate-700 bg-slate-900 p-4 shadow-2xl"
        onClick={(e) => e.stopPropagation()}>
        <div className="mb-3 flex items-baseline gap-2">
          <h2 className="text-lg font-bold">Kaufen: {symbol}</h2>
          <span className="truncate text-xs text-slate-500">{quote?.name}</span>
          <button onClick={onClose} className="ml-auto text-slate-500 hover:text-white">✕</button>
        </div>

        <div className="space-y-3 text-sm">
          <label className="flex flex-col gap-1 text-xs text-slate-400">
            Portfolio
            <select value={portfolioId ?? ""} onChange={(e) => setPortfolioId(Number(e.target.value))}
              className="rounded border border-slate-700 bg-slate-900 px-2 py-1.5 text-sm">
              {portfolios.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name} ({p.kind === "trial" ? "Trial" : p.kind === "auto" ? "Auto" : "Echt"})
                  {p.cash !== undefined ? ` — Cash ${fmt(p.cash)}` : ""}
                </option>
              ))}
            </select>
          </label>

          <div className="flex items-center gap-2">
            <div className="flex overflow-hidden rounded border border-slate-700 text-xs">
              <button onClick={() => setMode("budget")}
                className={`px-2 py-1 ${mode === "budget" ? "bg-sky-600 text-white" : "text-slate-400"}`}>
                Betrag
              </button>
              <button onClick={() => setMode("qty")}
                className={`px-2 py-1 ${mode === "qty" ? "bg-sky-600 text-white" : "text-slate-400"}`}>
                Stückzahl
              </button>
            </div>
            {mode === "budget" ? (
              <input value={budgetInput} onChange={(e) => setBudgetInput(e.target.value)}
                className="w-28 rounded border border-slate-700 bg-slate-900 px-2 py-1.5" autoFocus />
            ) : (
              <input value={qtyInput} onChange={(e) => setQtyInput(e.target.value)}
                className="w-28 rounded border border-slate-700 bg-slate-900 px-2 py-1.5" autoFocus />
            )}
            <label className="ml-auto flex items-center gap-1 text-xs text-slate-400">
              Kurs
              <input value={priceInput} onChange={(e) => setPriceInput(e.target.value)}
                placeholder={quote?.close != null ? fmt(quote.close) : "…"}
                className="w-24 rounded border border-slate-700 bg-slate-900 px-2 py-1.5" />
            </label>
          </div>

          <div className="rounded border border-slate-800 bg-slate-900/60 p-2 text-xs text-slate-400">
            <div className="flex justify-between"><span>Stück × Kurs</span>
              <span className="font-mono">{quantity || "—"} × {price ? fmt(price) : "—"} = {fmt(volume)}</span></div>
            <div className="flex justify-between">
              <span>Gebühr {platform ? `(${platform.name})` : "(keine Plattform)"}</span>
              <span className="font-mono">{fmt(fee)}</span>
            </div>
            <div className="mt-1 flex justify-between border-t border-slate-800 pt-1 font-semibold text-slate-200">
              <span>Gesamt</span><span className="font-mono">{fmt(total)} {quote?.currency || ""}</span>
            </div>
            {cashAfter !== undefined && (
              <div className={`flex justify-between ${cashAfter < 0 ? "text-rose-400" : ""}`}>
                <span>Cash danach</span><span className="font-mono">{fmt(cashAfter)}</span>
              </div>
            )}
            {(targetPrice || stopPrice) && (
              <div className="mt-1 text-slate-500">
                Signal-Zielzone: <span className="text-emerald-400">{targetPrice?.toFixed(2) ?? "—"}</span>
                {" / "}<span className="text-rose-400">{stopPrice?.toFixed(2) ?? "—"}</span>
              </div>
            )}
          </div>

          {error && <p className="text-xs text-rose-400">{error}</p>}

          <div className="flex justify-end gap-2">
            <button onClick={onClose} className="rounded border border-slate-700 px-3 py-1.5 text-sm text-slate-300">
              Abbrechen
            </button>
            <button onClick={buy} disabled={busy || quantity <= 0 || !portfolioId}
              className="rounded bg-emerald-600 px-4 py-1.5 text-sm font-semibold text-white hover:bg-emerald-500 disabled:opacity-50">
              {busy ? "Kaufe…" : "Kaufen"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
