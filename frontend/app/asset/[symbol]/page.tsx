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

type Catalyst = {
  type: string | null; title: string | null; date: string;
  importance: number | null; phase: string | null;
  indication: string | null; source_url: string | null;
};

type CustomEvent = {
  id: string; date: string; title: string; importance: number; url: string | null;
};

type Events = {
  earnings_dates: string[];
  ex_dividend_date: string | null;
  dividend_date: string | null;
  catalysts: Catalyst[];
  custom: CustomEvent[];
};

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
  const [events, setEvents] = useState<Events | null>(null);
  const [showProfile, setShowProfile] = useState(false);
  const [rangeDays, setRangeDays] = useState(365);
  const [running, setRunning] = useState(false);
  const [runResult, setRunResult] = useState<string | null>(null);
  const [portfolios, setPortfolios] = useState<{ id: number; name: string; kind: string }[]>([]);
  const [targetPortfolio, setTargetPortfolio] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api.get(`/api/assets/${symbol}/chart?days=${rangeDays}`).then(setChart).catch((e) => setError(e.message));
    api.get(`/api/assets/${symbol}/news`).then(setNews).catch(() => {});
    api.get(`/api/assets/${symbol}/analyses`).then(setAnalyses).catch(() => {});
    api.get(`/api/assets/${symbol}/profile`).then(setProfile).catch(() => {});
    api.get(`/api/assets/${symbol}/events`).then(setEvents).catch(() => {});
    api.get("/api/portfolios").then((p) => {
      setPortfolios(p);
      if (p.length > 0) setTargetPortfolio((cur: number | null) => cur ?? p[0].id);
    }).catch(() => {});
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
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <button
            onClick={async () => {
              setRunResult(null);
              try {
                await api.post("/api/watchlist", { symbol });
                setRunResult(`✅ ${symbol} zur Watchlist hinzugefügt.`);
              } catch (e: any) {
                setRunResult(e.status === 409 ? `${symbol} ist bereits auf der Watchlist.` : `❌ ${e.message}`);
              }
            }}
            className="rounded border border-slate-700 px-3 py-1.5 text-sm text-slate-300 hover:border-sky-500"
          >
            → Watchlist
          </button>
          {portfolios.length > 0 && (
            <span className="flex items-center gap-1">
              <select value={targetPortfolio ?? ""} onChange={(e) => setTargetPortfolio(Number(e.target.value))}
                className="rounded border border-slate-700 bg-slate-900 px-2 py-1.5 text-xs text-slate-300">
                {portfolios.map((p) => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
              </select>
              <button
                onClick={async () => {
                  if (targetPortfolio === null) return;
                  const qty = window.prompt(`Stückzahl für ${symbol}?`, "10");
                  if (!qty) return;
                  setRunResult(null);
                  try {
                    const res = await api.post(`/api/portfolios/${targetPortfolio}/positions`, {
                      symbol, quantity: parseFloat(qty.replace(",", ".")),
                    });
                    setRunResult(`✅ ${symbol} gekauft zu ${res.entry_price}.`);
                  } catch (e: any) {
                    setRunResult(`❌ ${e.message}`);
                  }
                }}
                className="rounded border border-slate-700 px-3 py-1.5 text-sm text-slate-300 hover:border-emerald-500"
              >
                Kaufen
              </button>
            </span>
          )}
          <button
            onClick={runNow}
            disabled={running}
            className="rounded bg-sky-600 px-3 py-1.5 text-sm font-semibold hover:bg-sky-500 disabled:opacity-50"
          >
            {running ? "Analysiere… (LLM)" : "Jetzt analysieren"}
          </button>
        </div>
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

      <EventsBar events={events} symbol={symbol}
        onChanged={() => api.get(`/api/assets/${symbol}/events`).then(setEvents).catch(() => {})} />

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

function EventsBar({ events, symbol, onChanged }: {
  events: Events | null; symbol: string; onChanged: () => void;
}) {
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({ date: "", title: "", importance: "7" });

  if (!events) return null;
  const today = new Date().toISOString().slice(0, 10);
  const nextEarnings = (events.earnings_dates || []).filter((d) => d >= today)[0];
  const exDiv = events.ex_dividend_date && events.ex_dividend_date >= today ? events.ex_dividend_date : null;
  const catalysts = (events.catalysts || []).filter((c) => c.date >= today).slice(0, 3);
  const custom = (events.custom || []).filter((c) => c.date >= today);

  const daysTo = (d: string) =>
    Math.round((new Date(d).getTime() - new Date(today).getTime()) / 86400000);

  async function addEvent(e: React.FormEvent) {
    e.preventDefault();
    if (!form.date || !form.title.trim()) return;
    await api.post(`/api/assets/${symbol}/events`, {
      date: form.date, title: form.title.trim(), importance: parseInt(form.importance) || 7,
    });
    setForm({ date: "", title: "", importance: "7" });
    setShowForm(false);
    onChanged();
  }

  async function removeEvent(id: string) {
    await api.del(`/api/events/${id}`);
    onChanged();
  }

  return (
    <div className="flex flex-wrap items-center gap-3 rounded-lg border border-amber-900/50 bg-amber-950/20 px-3 py-2 text-sm">
      <span className="text-xs font-semibold text-amber-400">📅 Termine</span>
      {nextEarnings && (
        <span className="text-slate-300">
          Quartalszahlen: <span className="font-semibold">{new Date(nextEarnings).toLocaleDateString("de-DE")}</span>
          <span className="ml-1 text-xs text-amber-400">(in {daysTo(nextEarnings)} Tagen{daysTo(nextEarnings) <= 14 ? " — Event-Risiko" : ""})</span>
        </span>
      )}
      {exDiv && (
        <span className="text-slate-300">
          Ex-Dividende: <span className="font-semibold">{new Date(exDiv).toLocaleDateString("de-DE")}</span>
        </span>
      )}
      {catalysts.map((c, i) => (
        <span key={i} className="text-slate-300" title={c.indication || ""}>
          💊 {c.source_url ? (
            <a href={c.source_url} target="_blank" rel="noreferrer" className="hover:text-sky-400">
              {c.title}
            </a>
          ) : c.title}
          : <span className="font-semibold">{new Date(c.date).toLocaleDateString("de-DE")}</span>
          {c.importance != null && (
            <span className="ml-1 text-xs text-amber-400">({c.importance}/10)</span>
          )}
        </span>
      ))}
      {custom.map((c) => (
        <span key={c.id} className="text-slate-300">
          📌 {c.title}: <span className="font-semibold">{new Date(c.date).toLocaleDateString("de-DE")}</span>
          <span className="ml-1 text-xs text-amber-400">({c.importance}/10)</span>
          <button onClick={() => removeEvent(c.id)} title="Termin löschen"
            className="ml-1 text-xs text-rose-400 hover:underline">✕</button>
        </span>
      ))}
      <button onClick={() => setShowForm(!showForm)} title="Eigenen Termin anlegen (Patentablauf, HV, …)"
        className="rounded border border-slate-700 px-2 py-0.5 text-xs text-slate-400 hover:border-amber-500">
        {showForm ? "abbrechen" : "+ Termin"}
      </button>
      {showForm && (
        <form onSubmit={addEvent} className="flex w-full flex-wrap items-center gap-2 pt-1">
          <input type="date" value={form.date} min={today}
            onChange={(e) => setForm({ ...form, date: e.target.value })}
            className="rounded border border-slate-700 bg-slate-900 px-2 py-1 text-xs" />
          <input value={form.title} placeholder="Titel (z.B. Patentablauf XYZ, Hauptversammlung)"
            onChange={(e) => setForm({ ...form, title: e.target.value })}
            className="w-72 rounded border border-slate-700 bg-slate-900 px-2 py-1 text-xs" />
          <label className="flex items-center gap-1 text-xs text-slate-500">
            Wichtigkeit
            <input value={form.importance} onChange={(e) => setForm({ ...form, importance: e.target.value })}
              className="w-10 rounded border border-slate-700 bg-slate-900 px-1 py-1 text-xs" />
            /10
          </label>
          <button className="rounded bg-sky-600 px-3 py-1 text-xs font-semibold hover:bg-sky-500">
            Speichern
          </button>
        </form>
      )}
    </div>
  );
}

function SentimentDot({ score }: { score: number | null }) {
  const color =
    score === null ? "bg-slate-600" : score > 0.2 ? "bg-emerald-500" : score < -0.2 ? "bg-rose-500" : "bg-slate-400";
  return <span className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${color}`} />;
}
