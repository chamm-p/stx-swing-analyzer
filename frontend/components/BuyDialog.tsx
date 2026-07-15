"use client";

import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";

type PortfolioOpt = {
  id: number; name: string; kind: string;
  platform_id: number | null; platform_name: string | null;
  cash?: number; start_capital?: number;
};
type Tier = {
  up_to: number | null; fee?: number; pct?: number; per_share?: number;
  min?: number; max?: number; max_pct?: number;
};
type Platform = { id: number; name: string; fees: Record<string, Tier[]> };

/** Gebühren-Vorschau (Client-Spiegel der Server-Staffel — der Server
 *  bucht beim Kauf autoritativ). Kennt Flat, Prozent und pro Aktie. */
function previewFee(platform: Platform | undefined, currency: string | null,
                    volume: number, quantity = 0): number {
  if (!platform || volume <= 0) return 0;
  const tiers = platform.fees[currency || ""] || platform.fees["default"] || [];
  const ordered = [...tiers].sort((a, b) =>
    (a.up_to === null ? 1 : 0) - (b.up_to === null ? 1 : 0) || (a.up_to ?? 0) - (b.up_to ?? 0));
  for (const t of ordered) {
    if (t.up_to === null || volume <= t.up_to) {
      let base = t.pct != null ? t.pct * volume
        : t.per_share != null ? t.per_share * quantity
        : (t.fee ?? 0);
      if (t.min != null) base = Math.max(base, t.min);
      if (t.max != null) base = Math.min(base, t.max);
      if (t.max_pct != null) base = Math.min(base, t.max_pct * volume);
      return Math.round(base * 100) / 100;
    }
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
  const [routeIbkr, setRouteIbkr] = useState(false);
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
    const fee = previewFee(platform, quote?.currency ?? null, budget, budget / price);
    return Math.max(Math.floor(((budget - fee) / price) * 10000) / 10000, 0);
  }, [mode, qtyInput, budgetInput, price, platform, quote]);

  const volume = quantity * price;
  const fee = previewFee(platform, quote?.currency ?? null, volume, quantity);
  const total = volume + fee;
  const cashAfter = portfolio?.cash !== undefined ? portfolio.cash - total : undefined;

  const ibkrPlatform = !!platform?.name?.toUpperCase().startsWith("IBKR");

  async function buy() {
    if (!portfolioId || quantity <= 0) return;
    setBusy(true);
    setError(null);
    try {
      let entryPrice: number | null = priceInput.trim() ? price : null;
      let ibkrNote = "";
      if (routeIbkr) {
        // Echte Order zuerst — nur bei Erfolg wird die Position gebucht.
        const order = await api.post("/api/broker/ibkr/order", {
          symbol, side: "BUY", quantity,
          order_type: "MKT",
          take_profit: targetPrice ?? null,
          stop_loss: stopPrice ?? null,
          currency: quote?.currency ?? null,
          confirm: true,
        });
        if (order.avg_fill_price) entryPrice = order.avg_fill_price;
        ibkrNote = ` · IBKR ${order.status}` +
          (order.avg_fill_price ? ` @ ${order.avg_fill_price}` : "") +
          (order.commission ? `, Kommission ${order.commission}` : "") +
          (order.bracket_orders ? `, ${order.bracket_orders} Bracket-Exits` : "");
      }
      const res = await api.post(`/api/portfolios/${portfolioId}/positions`, {
        symbol,
        quantity,
        entry_price: entryPrice,
      });
      onBought(`✅ ${quantity} × ${symbol} gekauft zu ${res.entry_price}` +
        (res.fee ? ` (+ ${res.fee} Gebühr)` : "") + ` → ${portfolio?.name}` + ibkrNote);
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

          {ibkrPlatform && (
            <label className="flex items-center gap-2 rounded border border-amber-900/60 bg-amber-950/20 p-2 text-xs"
              title="Platziert eine echte Market-Order über das IB Gateway; Ziel/Stop aus dem Signal gehen als Bracket-Exits mit. Erfordert „Orders erlauben“ in den IBKR-Einstellungen.">
              <input type="checkbox" checked={routeIbkr} onChange={(e) => setRouteIbkr(e.target.checked)} />
              <span className={routeIbkr ? "font-semibold text-amber-400" : "text-slate-400"}>
                🏦 Echte Order an IBKR senden (Market{(targetPrice || stopPrice) ? " + Bracket Ziel/Stop" : ""})
              </span>
            </label>
          )}

          {error && <p className="text-xs text-rose-400">{error}</p>}

          <div className="flex justify-end gap-2">
            <button onClick={onClose} className="rounded border border-slate-700 px-3 py-1.5 text-sm text-slate-300">
              Abbrechen
            </button>
            <button onClick={buy} disabled={busy || quantity <= 0 || !portfolioId}
              className={`rounded px-4 py-1.5 text-sm font-semibold text-white disabled:opacity-50 ${routeIbkr ? "bg-amber-600 hover:bg-amber-500" : "bg-emerald-600 hover:bg-emerald-500"}`}>
              {busy ? "Kaufe…" : routeIbkr ? "Order an IBKR senden" : "Kaufen"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
