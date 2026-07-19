"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import PriceDelta from "@/components/PriceDelta";

type PortfolioSummary = {
  id: number; name: string; kind: string; ibkr_sync?: boolean; open_positions: number;
  invested: number; value: number; pnl_abs: number; pnl_pct: number; realized_pnl: number;
  change_1d: number | null; change_7d: number | null;
  watch_enabled: boolean;
  platform_id: number | null; platform_name: string | null; fees_total: number;
  cash?: number; total_value?: number; total_pnl_abs?: number; total_pnl_pct?: number;
  config?: Record<string, any>;
};

const KIND_BADGE: Record<string, { label: string; cls: string }> = {
  real: { label: "ECHT", cls: "border-emerald-700 text-emerald-400" },
  trial: { label: "TRIAL", cls: "border-amber-600 text-amber-400" },
  auto: { label: "AUTO", cls: "border-sky-600 text-sky-400" },
};

export default function PortfoliosPage() {
  const [portfolios, setPortfolios] = useState<PortfolioSummary[]>([]);
  const [name, setName] = useState("");
  const [kind, setKind] = useState<"real" | "trial" | "auto">("real");
  const [platforms, setPlatforms] = useState<{ id: number; name: string }[]>([]);
  const [platformId, setPlatformId] = useState<number | "">("");
  const [cfg, setCfg] = useState({ start_capital: "10000", max_per_trade: "1000", max_positions: "10", min_confidence: "0.5", risk_pct: "1", min_crv: "1.5", use_screener: true, execution: "paper", ibkr_sync: false });
  const [startCapital, setStartCapital] = useState("");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api.get("/api/portfolios").then(setPortfolios).catch((e) => setError(e.message));
    api.get("/api/platforms").then(setPlatforms).catch(() => {});
  }, []);
  useEffect(load, [load]);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    try {
      const body: any = { name: name.trim(), kind, platform_id: platformId === "" ? null : platformId };
      if (kind !== "auto" && startCapital.trim()) {
        body.start_capital = parseFloat(startCapital.replace(",", "."));
      }
      if (kind === "auto") {
        body.config = {
          start_capital: parseFloat(cfg.start_capital.replace(",", ".")),
          max_per_trade: parseFloat(cfg.max_per_trade.replace(",", ".")),
          max_positions: parseInt(cfg.max_positions),
          min_confidence: parseFloat(cfg.min_confidence.replace(",", ".")),
          risk_pct: parseFloat(cfg.risk_pct.replace(",", ".")),
          min_crv: parseFloat(cfg.min_crv.replace(",", ".")),
          use_screener: cfg.use_screener,
          execution: cfg.execution,
          ibkr_sync: cfg.execution !== "paper" ? true : cfg.ibkr_sync,
          enabled: true,
        };
      }
      await api.post("/api/portfolios", body);
      setName("");
      load();
    } catch (err: any) {
      setError(err.message);
    }
  }

  async function toggleWatch(p: PortfolioSummary) {
    await api.patch(`/api/portfolios/${p.id}`, { watch_enabled: !p.watch_enabled });
    load();
  }

  async function remove(p: PortfolioSummary) {
    if (!confirm(`Portfolio "${p.name}" inkl. aller Positionen löschen?`)) return;
    await api.del(`/api/portfolios/${p.id}`);
    load();
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <h1 className="text-xl font-bold">Portfolios</h1>
        <Link href="/reports/tax"
          title="Realisierte Trades + Wertschriftenverzeichnis per 31.12. für die Steuererklärung (druckbar, CSV)"
          className="ml-auto rounded border border-slate-700 px-3 py-1 text-xs text-slate-300 hover:border-sky-500">
          🧾 Steuerreport
        </Link>
      </div>

      <form onSubmit={create} className="space-y-2">
        <div className="flex flex-wrap gap-2">
          <input value={name} onChange={(e) => setName(e.target.value)}
            placeholder='Name (z.B. "Depot comdirect", "Swing-Test Q3")'
            className="w-72 rounded border border-slate-700 bg-slate-900 px-3 py-2 text-sm outline-none focus:border-sky-500" />
          <select value={kind} onChange={(e) => setKind(e.target.value as any)}
            className="rounded border border-slate-700 bg-slate-900 px-3 py-2 text-sm">
            <option value="real">Echtes Portfolio</option>
            <option value="trial">Trial (Strategie-Test)</option>
            <option value="auto">Auto (System handelt selbst)</option>
          </select>
          <select value={platformId} onChange={(e) => setPlatformId(e.target.value === "" ? "" : Number(e.target.value))}
            title="Handelsplattform — deren Gebührenstaffel wird bei Käufen/Verkäufen gebucht"
            className="rounded border border-slate-700 bg-slate-900 px-3 py-2 text-sm">
            <option value="">ohne Gebühren</option>
            {platforms.map((p) => <option key={p.id} value={p.id}>Gebühren: {p.name}</option>)}
          </select>
          {kind !== "auto" && (
            <input value={startCapital} onChange={(e) => setStartCapital(e.target.value)}
              placeholder="Startkapital (optional)"
              title="Budget/Spielgeld — aktiviert Cash-Führung: Käufe buchen ab, Verkäufe schreiben gut"
              className="w-44 rounded border border-slate-700 bg-slate-900 px-3 py-2 text-sm outline-none focus:border-sky-500" />
          )}
          <button className="rounded bg-sky-600 px-4 py-2 text-sm font-semibold hover:bg-sky-500">Anlegen</button>
        </div>
        {kind === "auto" && (
          <div className="flex flex-wrap items-center gap-2 rounded border border-sky-900/60 bg-sky-950/30 p-3 text-sm">
            <CfgInput label="Startkapital" value={cfg.start_capital} onChange={(v) => setCfg({ ...cfg, start_capital: v })} />
            <CfgInput label="Max. pro Trade" value={cfg.max_per_trade} onChange={(v) => setCfg({ ...cfg, max_per_trade: v })} />
            <CfgInput label="Max. Positionen" value={cfg.max_positions} onChange={(v) => setCfg({ ...cfg, max_positions: v })} />
            <CfgInput label="Min. Confidence" value={cfg.min_confidence} onChange={(v) => setCfg({ ...cfg, min_confidence: v })} />
            <CfgInput label="Risiko %/Trade (1%-Regel)" value={cfg.risk_pct} onChange={(v) => setCfg({ ...cfg, risk_pct: v })} />
            <CfgInput label="Min. CRV" value={cfg.min_crv} onChange={(v) => setCfg({ ...cfg, min_crv: v })} />
            <label className="flex items-center gap-1 text-xs text-slate-400">
              <input type="checkbox" checked={cfg.use_screener}
                onChange={(e) => setCfg({ ...cfg, use_screener: e.target.checked })} />
              Screener-Signale handeln
            </label>
            <label className="flex flex-col text-xs text-slate-400">
              Ausführung
              <select value={cfg.execution} onChange={(e) => setCfg({ ...cfg, execution: e.target.value })}
                className="mt-0.5 w-52 rounded border border-slate-700 bg-slate-900 px-2 py-1 text-sm text-slate-100">
                <option value="paper">Paper (simuliert, kein echtes Geld)</option>
                <option value="manual">IBKR manuell (nur Vorschläge)</option>
                <option value="ibkr">IBKR automatisch (echte Orders) ⚠️</option>
              </select>
            </label>
            <span className="w-full text-xs text-slate-500">
              {cfg.execution === "paper" && "Simuliert: kauft BUY-Signale, verkauft bei SELL/Horizont. Kein echtes Geld."}
              {cfg.execution === "manual" && "IBKR-synchronisiert: das System schlägt Trades vor (Telegram/E-Mail), du führst sie im Kauf-/Verkauf-Dialog aus."}
              {cfg.execution === "ibkr" && "⚠️ ECHTE Orders: das System handelt automatisch über IBKR. Zusätzlich muss „Orders erlauben“ in den IBKR-Einstellungen aktiv sein."}
            </span>
          </div>
        )}
      </form>
      {error && <p className="text-sm text-rose-400">{error}</p>}

      <div className="grid gap-4 sm:grid-cols-2">
        {portfolios.map((p) => (
          <div key={p.id} className="rounded-lg border border-slate-800 bg-slate-900/50 p-4">
            <div className="flex items-start justify-between">
              <Link href={`/portfolios/${p.id}`} className="text-lg font-bold text-sky-400 hover:underline">
                {p.name}
              </Link>
              <span className={`rounded border px-2 py-0.5 text-xs ${(KIND_BADGE[p.kind] || KIND_BADGE.real).cls}`}>
                {(KIND_BADGE[p.kind] || KIND_BADGE.real).label}
              </span>
            </div>
            {p.kind === "auto" && (
              <div className="mt-1 flex items-center gap-2 text-xs">
                <span className="text-slate-500">Ausführung:</span>
                <select value={p.config?.execution || "paper"}
                  onChange={async (e) => {
                    const v = e.target.value;
                    if (v === "ibkr" && !confirm(`„${p.name}“ auf ECHTE automatische IBKR-Orders umstellen?\n\nDas System handelt dann selbstständig mit echtem Geld (zusätzlich muss „Orders erlauben“ in den IBKR-Einstellungen aktiv sein).`)) return;
                    await api.patch(`/api/portfolios/${p.id}`, {
                      config: { ...p.config, execution: v,
                        ibkr_sync: v !== "paper" ? true : !!p.config?.ibkr_sync },
                    });
                    load();
                  }}
                  className={`rounded border bg-slate-900 px-2 py-0.5 ${p.config?.execution === "ibkr" ? "border-amber-600 text-amber-400" : "border-slate-700 text-slate-300"}`}>
                  <option value="paper">Paper</option>
                  <option value="manual">IBKR manuell</option>
                  <option value="ibkr">IBKR automatisch ⚠️</option>
                </select>
              </div>
            )}
            {(p.kind === "real" || p.kind === "auto") && (
              <div className="mt-1 flex items-center gap-2 text-xs text-slate-500">
                <label className="flex items-center gap-1"
                  title="IBKR-Bestände automatisch spiegeln (stündlich, read-only): neue Positionen mit echtem Einstand, extern Verkauftes wird geschlossen, Cash übernommen">
                  <input type="checkbox" checked={!!p.ibkr_sync}
                    onChange={async (e) => {
                      await api.patch(`/api/portfolios/${p.id}`, { ibkr_sync: e.target.checked });
                      load();
                    }} />
                  🏦 IBKR-Sync
                </label>
                <button
                  onClick={async () => {
                    try {
                      const r = await api.post(`/api/portfolios/${p.id}/ibkr-sync`);
                      alert(`⟳ IBKR-Sync: ${r.added} neu, ${r.updated} angepasst, ${r.closed} geschlossen` +
                        ` (IBKR: ${r.ibkr_positions} Positionen${r.cash != null ? `, Cash ${r.cash}` : ""})`);
                      load();
                    } catch (e: any) {
                      alert(`❌ ${e.message}`);
                    }
                  }}
                  title="Jetzt sofort abgleichen (auch als einmaliger Import nutzbar)"
                  className="rounded border border-slate-700 px-2 py-0.5 hover:border-sky-500">
                  ⟳ jetzt
                </button>
              </div>
            )}
            {p.config?.strategy && (
              <div className="mt-1 flex items-center gap-2 text-xs text-sky-400/80"
                title={Object.entries(p.config.strategy).map(([k, v]) => `${k}=${v}`).join(", ")}>
                🧪 Challenger-Strategie (Schwelle {p.config.strategy.threshold}, eigenes Scoring)
                <button
                  onClick={async () => {
                    if (!confirm(`Strategie-Parameter von „${p.name}" als globale Live-Signallogik übernehmen?\n\nGilt danach für Screener, Analysen und Zielzonen — nicht nur für dieses Portfolio.`)) return;
                    try {
                      const r = await api.post(`/api/portfolios/${p.id}/promote`);
                      alert(`🏆 Champion übernommen: ${Object.entries(r.new).map(([k, v]) => `${k}=${v}`).join(", ")}\n(vorher: ${r.old ? Object.entries(r.old).map(([k, v]) => `${k}=${v}`).join(", ") : "Defaults"})`);
                    } catch (e: any) {
                      alert(`❌ ${e.message}`);
                    }
                  }}
                  title="Bewährte Challenger-Parameter global in die Live-Signallogik übernehmen (Schwelle, Ziel-/Stop-Faktoren)"
                  className="rounded border border-amber-700/60 px-2 py-0.5 text-amber-400 hover:border-amber-500">
                  🏆 Zum Champion machen
                </button>
              </div>
            )}
            {p.total_value !== undefined && (
              <div className="mt-2 text-sm">
                <span className="font-semibold">{p.total_value.toLocaleString("de-DE", { maximumFractionDigits: 0 })}</span>
                <span className="text-xs text-slate-500"> gesamt (davon Cash {p.cash?.toLocaleString("de-DE", { maximumFractionDigits: 0 })})</span>
                <span className={`ml-2 font-semibold ${(p.total_pnl_abs ?? 0) >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                  {(p.total_pnl_abs ?? 0) >= 0 ? "+" : ""}{p.total_pnl_pct?.toFixed(1)}% seit Start
                </span>
              </div>
            )}
            <div className="mt-3 grid grid-cols-3 gap-2 text-sm">
              <div>
                <div className="font-semibold">
                  {p.value.toLocaleString("de-DE", { maximumFractionDigits: 0 })}
                  <span title="Wertänderung des Gesamtportfolios (offene Positionen; Cash dämpft) — wechselt zwischen Vortag (1T) und 7 Tagen (7T)">
                    <PriceDelta d1={p.change_1d} d7={p.change_7d} className="ml-2 text-xs" />
                  </span>
                </div>
                <div className="text-xs text-slate-500">Wert</div>
              </div>
              <div>
                <div className={`font-semibold ${p.pnl_abs >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                  {p.pnl_abs >= 0 ? "+" : ""}{p.pnl_abs.toLocaleString("de-DE", { maximumFractionDigits: 0 })} ({p.pnl_pct.toFixed(1)}%)
                </div>
                <div className="text-xs text-slate-500">Unrealisiert</div>
              </div>
              <div>
                <div className="font-semibold">{p.open_positions}</div>
                <div className="text-xs text-slate-500">Positionen</div>
              </div>
            </div>
            <div className="mt-2 flex items-center gap-2 text-xs text-slate-500">
              <select
                value={p.platform_id ?? ""}
                onChange={async (e) => {
                  await api.patch(`/api/portfolios/${p.id}`, {
                    platform_id: e.target.value === "" ? -1 : Number(e.target.value),
                  });
                  load();
                }}
                title="Handelsplattform — Gebührenstaffel für künftige Käufe/Verkäufe"
                className="rounded border border-slate-700 bg-slate-900 px-2 py-0.5 text-xs"
              >
                <option value="">Gebühren: keine</option>
                {platforms.map((pl) => (
                  <option key={pl.id} value={pl.id}>Gebühren: {pl.name}</option>
                ))}
              </select>
              {p.fees_total > 0 && <span>bisher {p.fees_total.toLocaleString("de-DE")} gezahlt</span>}
            </div>
            <div className="mt-3 flex items-center gap-3 text-xs text-slate-500">
              <button
                onClick={() => toggleWatch(p)}
                title="Offene Positionen automatisch analysieren (Watchlist, Signale, Dashboard)"
                className={`rounded border px-2 py-1 ${p.watch_enabled ? "border-emerald-700 text-emerald-400" : "border-slate-700"}`}
              >
                Beobachten {p.watch_enabled ? "an" : "aus"}
              </button>
              {p.realized_pnl !== 0 && (
                <span>Realisiert: {p.realized_pnl >= 0 ? "+" : ""}{p.realized_pnl.toLocaleString("de-DE")}</span>
              )}
              <button onClick={() => remove(p)} className="ml-auto text-rose-400 hover:underline">Löschen</button>
            </div>
          </div>
        ))}
      </div>
      {portfolios.length === 0 && !error && (
        <p className="text-slate-500">
          Noch keine Portfolios. Tipp: ein „echtes" für laufende Positionen und ein „Trial" zum Strategie-Testen anlegen.
        </p>
      )}
    </div>
  );
}

function CfgInput({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
  return (
    <label className="flex flex-col text-xs text-slate-400">
      {label}
      <input value={value} onChange={(e) => onChange(e.target.value)}
        className="w-28 rounded border border-slate-700 bg-slate-900 px-2 py-1 text-sm text-slate-100 outline-none focus:border-sky-500" />
    </label>
  );
}
