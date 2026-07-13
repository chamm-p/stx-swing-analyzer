"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { api, runAnalysis, Signal } from "@/lib/api";
import SignalBadge from "@/components/SignalBadge";
import PriceChart from "@/components/PriceChart";

type ChartData = {
  candles: any[];
  indicators: Record<string, { time: string; value: number }[]>;
  snapshot: Record<string, number | null>;
  signals: Signal[];
};

type NewsItem = {
  id: string; title: string; url: string | null; source: string | null;
  published_at: string; sentiment_score: number | null; sentiment_label: string | null;
  sentiment_rationale: string | null;
};

type Analysis = { id: string; ts: string; model: string | null; payload: any };

type Profile = {
  symbol: string; name: string; sector: string | null; industry: string | null;
  employees: number | null; website: string | null; city: string | null; country: string | null;
  summary: string | null; summary_de?: string; market_cap: number | null;
  trailing_pe: number | null; forward_pe: number | null; dividend_yield: number | null;
  beta: number | null; fifty_two_week_high: number | null; fifty_two_week_low: number | null;
  total_revenue: number | null; profit_margin: number | null; currency: string | null;
};

export default function AssetPage() {
  const { symbol } = useParams<{ symbol: string }>();
  const router = useRouter();
  const [chart, setChart] = useState<ChartData | null>(null);
  const [news, setNews] = useState<NewsItem[]>([]);
  const [analyses, setAnalyses] = useState<Analysis[]>([]);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [showProfile, setShowProfile] = useState(false);
  const [rangeDays, setRangeDays] = useState(365);
  const [running, setRunning] = useState(false);
  const [runResult, setRunResult] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api.get(`/api/assets/${symbol}/chart?days=${rangeDays}`).then(setChart).catch((e) => setError(e.message));
    api.get(`/api/assets/${symbol}/news`).then(setNews).catch(() => {});
    api.get(`/api/assets/${symbol}/analyses`).then(setAnalyses).catch(() => {});
    api.get(`/api/assets/${symbol}/profile`).then(setProfile).catch(() => {});
  }, [symbol, rangeDays]);
  useEffect(load, [load]);

  async function runNow() {
    setRunning(true);
    setRunResult(null);
    setError(null);
    try {
      const res = await runAnalysis(symbol);
      if (res.created && res.signal) {
        setRunResult(`✅ Analyse abgeschlossen — neues Signal: ${res.signal.action} (${Math.round(res.signal.confidence * 100)}%)`);
      } else {
        setRunResult("✅ Analyse abgeschlossen — Einschätzung unverändert, kein neues Signal (Dedupe-Fenster).");
      }
      load();
    } catch (e: any) {
      setRunResult(`❌ Analyse fehlgeschlagen: ${e.message}`);
    } finally {
      setRunning(false);
    }
  }

  const latestSignal = chart?.signals?.at(-1);
  const latestReview = analyses[0]?.payload;

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-4">
        <button
          onClick={() => (window.history.length > 1 ? router.back() : router.push("/"))}
          title="Zurück"
          aria-label="Zurück"
          className="rounded-full border border-slate-700 px-3 py-1.5 text-lg leading-none text-slate-300 hover:border-sky-500 hover:text-white"
        >
          ←
        </button>
        <div>
          <h1 className="text-2xl font-bold">{symbol}</h1>
          {profile && (
            <div className="text-sm text-slate-400">
              {profile.name}
              {profile.sector && <span> · {profile.sector}</span>}
              {profile.industry && <span> · {profile.industry}</span>}
            </div>
          )}
        </div>
        {latestSignal && <SignalBadge action={latestSignal.action} confidence={latestSignal.confidence} />}
        <button
          onClick={runNow}
          disabled={running}
          className="ml-auto rounded bg-sky-600 px-3 py-1.5 text-sm font-semibold hover:bg-sky-500 disabled:opacity-50"
        >
          {running ? "Analysiere… (LLM)" : "Jetzt analysieren"}
        </button>
      </div>
      {error && <p className="text-sm text-rose-400">{error}</p>}
      {runResult && <p className="text-sm text-amber-400">{runResult}</p>}

      <div className="flex gap-2">
        {[
          { label: "6M", days: 180 },
          { label: "1J", days: 365 },
          { label: "2J", days: 730 },
          { label: "Max", days: 36500 },
        ].map((r) => (
          <button key={r.label} onClick={() => setRangeDays(r.days)}
            className={`rounded-full border px-3 py-1 text-xs font-semibold ${
              rangeDays === r.days
                ? "border-sky-500 bg-sky-600/20 text-sky-300"
                : "border-slate-700 text-slate-400 hover:border-slate-500"
            }`}>
            {r.label}
          </button>
        ))}
      </div>

      <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-2">
        {chart ? (
          <PriceChart candles={chart.candles} indicators={chart.indicators} signals={chart.signals} />
        ) : (
          <div className="h-[420px] animate-pulse" />
        )}
      </div>

      {chart?.snapshot && Object.keys(chart.snapshot).length > 0 && (
        <div className="grid grid-cols-3 gap-2 sm:grid-cols-6">
          {[
            ["Kurs", chart.snapshot.close],
            ["RSI(14)", chart.snapshot.rsi14],
            ["MACD-Hist", chart.snapshot.macd_hist],
            ["SMA50", chart.snapshot.sma50],
            ["SMA200", chart.snapshot.sma200],
            ["Δ 20d %", chart.snapshot.pct_change_20d],
          ].map(([label, value]) => (
            <div key={String(label)} className="rounded border border-slate-800 bg-slate-900/50 p-2 text-center">
              <div className="text-sm font-semibold">{value === null || value === undefined ? "—" : Number(value).toFixed(2)}</div>
              <div className="text-xs text-slate-500">{label}</div>
            </div>
          ))}
        </div>
      )}

      {profile && (
        <section className="rounded-lg border border-slate-800 bg-slate-900/50 p-4">
          <button onClick={() => setShowProfile(!showProfile)} className="flex w-full items-center gap-2 text-left">
            <h2 className="font-semibold">Unternehmensprofil</h2>
            <span className="text-xs text-slate-500">
              {profile.city && `${profile.city}, `}{profile.country}
              {profile.employees && ` · ${profile.employees.toLocaleString("de-DE")} Mitarbeiter`}
            </span>
            <span className="ml-auto text-slate-500">{showProfile ? "▲" : "▼"}</span>
          </button>
          {showProfile && (
            <div className="mt-3 space-y-4">
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                <ProfileStat label="Marktkapitalisierung" value={fmtBig(profile.market_cap, profile.currency)} />
                <ProfileStat label="Umsatz (TTM)" value={fmtBig(profile.total_revenue, profile.currency)} />
                <ProfileStat label="KGV / Forward" value={`${fmtVal(profile.trailing_pe)} / ${fmtVal(profile.forward_pe)}`} />
                <ProfileStat label="Dividendenrendite" value={fmtPct(profile.dividend_yield)} />
                <ProfileStat label="Nettomarge" value={fmtPct(profile.profit_margin)} />
                <ProfileStat label="Beta" value={fmtVal(profile.beta)} />
                <ProfileStat label="52W Hoch/Tief" value={`${fmtVal(profile.fifty_two_week_high)} / ${fmtVal(profile.fifty_two_week_low)}`} />
                <ProfileStat label="Website" value={profile.website ? (
                  <a href={profile.website} target="_blank" rel="noreferrer" className="text-sky-400 hover:underline">
                    {profile.website.replace(/^https?:\/\/(www\.)?/, "")}
                  </a>
                ) : "—"} />
              </div>
              {(profile.summary_de || profile.summary) && (
                <p className="text-sm leading-relaxed text-slate-300">
                  {profile.summary_de || profile.summary}
                  {!profile.summary_de && (
                    <span className="ml-2 text-xs text-slate-500">
                      (englisches Original — deutsche Übersetzung erscheint, sobald ein LLM konfiguriert ist)
                    </span>
                  )}
                </p>
              )}
            </div>
          )}
        </section>
      )}

      {latestReview && (
        <section className="rounded-lg border border-slate-800 bg-slate-900/50 p-4">
          <h2 className="mb-2 font-semibold">LLM-Einschätzung <span className="text-xs text-slate-500">({analyses[0].model}, {new Date(analyses[0].ts).toLocaleString("de-DE")})</span></h2>
          <p className="text-sm text-slate-300">{latestReview.summary}</p>
          {latestReview.technical_view && (
            <p className="mt-2 text-sm text-slate-400"><span className="font-semibold">Technisch:</span> {latestReview.technical_view}</p>
          )}
          {Array.isArray(latestReview.key_risks) && latestReview.key_risks.length > 0 && (
            <ul className="mt-2 list-inside list-disc text-sm text-amber-400/80">
              {latestReview.key_risks.map((r: string, i: number) => <li key={i}>{r}</li>)}
            </ul>
          )}
        </section>
      )}

      {(chart?.signals?.length ?? 0) > 0 && (
        <section>
          <h2 className="mb-2 font-semibold">Signal-Historie</h2>
          <div className="space-y-2">
            {[...chart!.signals].reverse().map((s) => (
              <div key={s.id} className="rounded border border-slate-800 bg-slate-900/40 p-3 text-sm">
                <div className="flex flex-wrap items-center gap-3">
                  <SignalBadge action={s.action} confidence={s.confidence} />
                  <span className="text-slate-400">{new Date(s.ts).toLocaleString("de-DE")}</span>
                  <span className="text-slate-400">Kurs {s.price_at_signal ?? "—"}</span>
                  {s.target_price && (
                    <span className="text-slate-400">
                      Ziel <span className="text-emerald-400">{s.target_price.toFixed(2)}</span>
                      {" · "}Stop <span className="text-rose-400">{s.stop_price?.toFixed(2)}</span>
                      {" · "}CRV 1:{s.risk_reward}
                    </span>
                  )}
                  {s.analyst_target && (
                    <span className="text-xs text-slate-500" title={`${s.analyst_count} Analystenschätzungen`}>
                      Analysten: {s.analyst_target.toFixed(2)}
                    </span>
                  )}
                </div>
                {s.rationale && <p className="mt-1 text-xs text-slate-400">{s.rationale}</p>}
              </div>
            ))}
          </div>
        </section>
      )}

      <section>
        <h2 className="mb-2 font-semibold">News & Sentiment</h2>
        {news.length === 0 ? (
          <p className="text-sm text-slate-500">Noch keine zugeordneten News.</p>
        ) : (
          <div className="space-y-2">
            {news.map((n) => (
              <div key={n.id} className="rounded border border-slate-800 bg-slate-900/40 p-3">
                <div className="flex items-start gap-2">
                  <SentimentDot score={n.sentiment_score} />
                  <div className="min-w-0">
                    <a href={n.url || "#"} target="_blank" rel="noreferrer" className="text-sm font-medium hover:text-sky-400">
                      {n.title}
                    </a>
                    <div className="text-xs text-slate-500">
                      {n.source} · {new Date(n.published_at).toLocaleString("de-DE")}
                      {n.sentiment_score !== null && ` · Sentiment ${n.sentiment_score > 0 ? "+" : ""}${n.sentiment_score.toFixed(2)}`}
                    </div>
                    {n.sentiment_rationale && <p className="mt-1 text-xs text-slate-400">{n.sentiment_rationale}</p>}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

function fmtVal(v: number | null) {
  return v === null || v === undefined ? "—" : Number(v).toLocaleString("de-DE", { maximumFractionDigits: 2 });
}

function fmtPct(v: number | null) {
  if (v === null || v === undefined) return "—";
  // yfinance liefert Anteile (0.032) — Werte > 1 sind bereits Prozent
  const pct = v > 1 ? v : v * 100;
  return `${pct.toLocaleString("de-DE", { maximumFractionDigits: 2 })}%`;
}

function fmtBig(v: number | null, currency: string | null) {
  if (v === null || v === undefined) return "—";
  const cur = currency ? ` ${currency}` : "";
  if (v >= 1e12) return `${(v / 1e12).toLocaleString("de-DE", { maximumFractionDigits: 2 })} Bio.${cur}`;
  if (v >= 1e9) return `${(v / 1e9).toLocaleString("de-DE", { maximumFractionDigits: 1 })} Mrd.${cur}`;
  if (v >= 1e6) return `${(v / 1e6).toLocaleString("de-DE", { maximumFractionDigits: 1 })} Mio.${cur}`;
  return `${v.toLocaleString("de-DE")}${cur}`;
}

function ProfileStat({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="rounded border border-slate-800 bg-slate-900/40 p-2">
      <div className="truncate text-sm font-semibold">{value}</div>
      <div className="text-xs text-slate-500">{label}</div>
    </div>
  );
}

function SentimentDot({ score }: { score: number | null }) {
  const color =
    score === null ? "bg-slate-600" : score > 0.2 ? "bg-emerald-500" : score < -0.2 ? "bg-rose-500" : "bg-slate-400";
  return <span className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${color}`} />;
}
