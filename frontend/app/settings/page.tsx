"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";

type LlmView = {
  provider: string; base_url: string; model: string;
  reasoning_mode: string; has_api_key: boolean;
};
type CommView = {
  smtp_host: string; smtp_port: string | number; smtp_user: string; smtp_from: string;
  alert_email_to: string; has_smtp_password: boolean;
  telegram_chat_id: string; has_telegram_bot_token: boolean;
};

export default function SettingsPage() {
  const [llm, setLlm] = useState<LlmView | null>(null);
  const [comm, setComm] = useState<CommView | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api.get("/api/settings").then((d) => {
      setLlm(d.llm);
      setComm(d.comm);
    }).catch((e) => setError(e.message));
  }, []);
  useEffect(load, [load]);

  if (error) return <p className="text-rose-400">Fehler: {error}</p>;

  return (
    <div className="space-y-8">
      <h1 className="text-xl font-bold">Einstellungen</h1>
      <p className="text-sm text-slate-500">
        Werte überschreiben die <code>.env</code>-Defaults und gelten sofort für Backend und Worker.
        Schlüssel/Passwörter werden verschlüsselt gespeichert und hier nie angezeigt.
      </p>
      {llm && <LlmSection initial={llm} onSaved={load} />}
      {comm && <CommSection initial={comm} onSaved={load} />}
      <JobsSection />
      <IbkrSection />
      <RedditSection />
      <PlatformsSection />
      <McpSection />
      <SourcesSection />
    </div>
  );
}

/* ------------------------------------------------------------ Jobs/Zeitpläne */

type JobInfo = {
  id: string; label: string; unit: string; setting: string | null;
  interval: string; editable: boolean; running: boolean;
  last_run: { ts: number; ok: boolean; info?: string; duration_s?: number } | null;
  next_run: string | null;
};

const UNIT_LABEL: Record<string, string> = {
  min: "Min", days: "Tage", time: "Uhr (UTC)", times: "Uhr (UTC, Komma-Liste)",
};

function JobsSection() {
  const [jobs, setJobs] = useState<JobInfo[]>([]);
  const [segments, setSegments] = useState("");
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [msg, setMsg] = useState<string | null>(null);

  const load = useCallback(() => {
    api.get("/api/jobs").then((d) => {
      setJobs(d.jobs);
      setSegments(d.optimize_segments);
    }).catch((e) => setMsg(e.message));
  }, []);
  useEffect(() => {
    load();
    const t = setInterval(load, 30000);
    return () => clearInterval(t);
  }, [load]);

  async function saveIntervals() {
    setMsg(null);
    const payload: Record<string, string> = {};
    for (const j of jobs) {
      if (j.setting && edits[j.id] !== undefined && edits[j.id] !== j.interval) {
        payload[j.setting] = edits[j.id];
      }
    }
    if (segments) payload.optimize_segments = segments;
    try {
      await api.put("/api/settings/scheduler", payload);
      setEdits({});
      setMsg("✅ Gespeichert — der Worker übernimmt die Zeitpläne binnen 20 s.");
      load();
    } catch (e: any) {
      setMsg(e.message);
    }
  }

  async function runNow(id: string) {
    setMsg(null);
    try {
      await api.post(`/api/jobs/${id}/run`);
      setMsg(`▶ ${id} angestoßen — Start binnen 20 s, Status aktualisiert sich hier.`);
      setTimeout(load, 25000);
    } catch (e: any) {
      setMsg(e.message);
    }
  }

  function lastRunCell(j: JobInfo) {
    if (j.running) return <span className="text-amber-400">⏳ läuft…</span>;
    const lr = j.last_run;
    if (!lr) return <span className="text-slate-600">—</span>;
    const when = new Date(lr.ts * 1000).toLocaleString("de-DE",
      { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
    return (
      <span title={`${lr.info || ""}${lr.duration_s ? ` (${Math.round(lr.duration_s)}s)` : ""}`}>
        {lr.ok ? "✅" : "❌"} {when}
      </span>
    );
  }

  return (
    <section className="rounded-lg border border-slate-800 bg-slate-900/50 p-4">
      <div className="mb-1 flex items-center gap-3">
        <h2 className="font-semibold">⏱️ Zeitpläne & Jobs</h2>
        <span className="text-xs text-slate-500">
          Intervalle greifen ohne Neustart · „▶" startet den Job sofort im Worker
        </span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-left text-xs text-slate-500">
            <tr>
              <th className="py-1 pr-3">Job</th>
              <th className="py-1 pr-3">Intervall</th>
              <th className="py-1 pr-3">Letzter Lauf</th>
              <th className="py-1 pr-3">Nächster Lauf</th>
              <th className="py-1"></th>
            </tr>
          </thead>
          <tbody>
            {jobs.map((j) => (
              <tr key={j.id} className="border-t border-slate-800">
                <td className="py-1.5 pr-3">{j.label}</td>
                <td className="py-1.5 pr-3">
                  {j.editable ? (
                    <span className="flex items-center gap-1">
                      <input
                        value={edits[j.id] ?? j.interval}
                        onChange={(e) => setEdits((s) => ({ ...s, [j.id]: e.target.value }))}
                        className={`${j.unit === "times" ? "w-28" : "w-20"} rounded border border-slate-700 bg-slate-900 px-2 py-0.5 text-xs`}
                        title={j.unit === "days" ? "0 = deaktiviert" : undefined}
                      />
                      <span className="text-xs text-slate-500">{UNIT_LABEL[j.unit]}</span>
                    </span>
                  ) : (
                    <span className="text-xs text-slate-500">{j.interval} {UNIT_LABEL[j.unit]} (fix)</span>
                  )}
                </td>
                <td className="py-1.5 pr-3 text-xs">{lastRunCell(j)}</td>
                <td className="py-1.5 pr-3 text-xs text-slate-400">
                  {j.next_run
                    ? new Date(j.next_run).toLocaleString("de-DE", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })
                    : <span className="text-slate-600">pausiert</span>}
                </td>
                <td className="py-1.5 text-right">
                  <button onClick={() => runNow(j.id)} disabled={j.running}
                    title="Sofort im Worker starten"
                    className="rounded border border-slate-700 px-2 py-0.5 text-xs text-slate-300 hover:border-emerald-500 disabled:opacity-40">
                    ▶
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <label className="text-xs text-slate-500">Optimierungs-Segmente:</label>
        <input value={segments} onChange={(e) => setSegments(e.target.value)}
          title='Komma-getrennt; "+" gruppiert zu einem Lauf (z.B. US+NASDAQ100,DAX,CRYPTO)'
          className="w-64 rounded border border-slate-700 bg-slate-900 px-2 py-1 text-xs" />
        <button onClick={saveIntervals}
          className="rounded bg-sky-600 px-3 py-1 text-xs font-semibold hover:bg-sky-500">
          Speichern
        </button>
        {msg && <span className="text-xs text-amber-400">{msg}</span>}
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------- IBKR */

function IbkrSection() {
  const [cfg, setCfg] = useState<Record<string, any> | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<any>(null);
  const [testing, setTesting] = useState(false);

  useEffect(() => {
    api.get("/api/settings/ibkr").then(setCfg).catch(() => setCfg({}));
  }, []);

  if (!cfg) return null;
  const tradingOn = String(cfg.trading_enabled).toLowerCase() === "true";

  async function save() {
    setMsg(null);
    try {
      const saved = await api.put("/api/settings/ibkr", {
        account: String(cfg!.account ?? ""),
        consumer_key: String(cfg!.consumer_key ?? ""),
        access_token: String(cfg!.access_token ?? ""),
        access_token_secret: cfg!.access_token_secret || undefined,
        trading_enabled: String(cfg!.trading_enabled ?? "false"),
      });
      setCfg({ ...saved, access_token_secret: "" });
      setMsg("✅ Gespeichert.");
    } catch (e: any) {
      setMsg(e.message);
    }
  }

  async function test() {
    setTesting(true);
    setTestResult(null);
    setMsg(null);
    try {
      setTestResult(await api.get("/api/broker/ibkr/status"));
    } catch (e: any) {
      setMsg(`❌ ${e.message}`);
    } finally {
      setTesting(false);
    }
  }

  const field = (key: string, label: string, hint?: string) => (
    <Field label={label}>
      <input value={cfg[key] ?? ""} title={hint}
        onChange={(e) => setCfg({ ...cfg, [key]: e.target.value })}
        className={inputCls} />
    </Field>
  );

  return (
    <section className="rounded-lg border border-slate-800 bg-slate-900/50 p-4">
      <h2 className="mb-1 font-semibold">🏦 IBKR (Web-API, OAuth)</h2>
      <p className="mb-3 text-xs text-slate-500">
        Headless über die IBKR-Web-API — kein Gateway. Einrichtung im IBKR-Self-Service-OAuth-Portal;
        die privaten Schlüssel (<code>private_signature.pem</code>, <code>private_encryption.pem</code>,{" "}
        <code>dhparam.pem</code>) liegen auf dem Server unter <code>secrets/ibkr/</code>.
        Neue Consumer-Keys aktiviert IBKR erst beim Wochenend-Neustart.
      </p>
      {Array.isArray(cfg.missing) && cfg.missing.length > 0 && (
        <p className="mb-3 rounded border border-amber-900/60 bg-amber-950/20 p-2 text-xs text-amber-400">
          ⚠️ Noch unvollständig: {cfg.missing.join(", ")}
        </p>
      )}
      <FieldGrid>
        {field("consumer_key", "Consumer-Key", "9 Zeichen, im OAuth-Portal selbst vergeben")}
        {field("access_token", "Access-Token", "aus dem OAuth-Portal (Generate Token)")}
        <Field label={`Access-Token-Secret ${cfg.has_access_token_secret ? "(gespeichert — leer = behalten)" : ""}`}>
          <input type="password" value={cfg.access_token_secret ?? ""}
            placeholder={cfg.has_access_token_secret ? "••••••••" : ""}
            onChange={(e) => setCfg({ ...cfg, access_token_secret: e.target.value })}
            className={inputCls} />
        </Field>
        {field("account", "Konto-ID (optional)", "z.B. U1234567 bzw. DU… für Paper; leer = erstes Konto")}
        <label className="flex items-center gap-2 self-end pb-2 text-xs"
          title="Ohne Haken ist die Verbindung faktisch read-only — kein Endpoint kann Orders senden">
          <input type="checkbox" checked={tradingOn}
            onChange={(e) => setCfg({ ...cfg, trading_enabled: e.target.checked ? "true" : "false" })} />
          <span className={tradingOn ? "font-semibold text-amber-400" : "text-slate-400"}>
            Orders erlauben {tradingOn && "⚠️"}
          </span>
        </label>
      </FieldGrid>
      <div className="mt-3 flex items-center gap-3">
        <button onClick={save} className="rounded bg-sky-600 px-4 py-2 text-sm font-semibold hover:bg-sky-500">
          Speichern
        </button>
        <button onClick={test} disabled={testing}
          className="rounded border border-slate-700 px-3 py-2 text-sm text-slate-300 hover:border-emerald-500 disabled:opacity-50">
          {testing ? "Verbinde…" : "Verbindung testen"}
        </button>
      </div>
      {msg && <p className="mt-2 text-sm text-amber-400">{msg}</p>}
      {testResult && (
        <div className="mt-3 rounded border border-emerald-900/60 bg-emerald-950/20 p-3 text-xs">
          <div className="mb-1 font-semibold text-emerald-400">
            ✅ Verbunden ({testResult.api || "IBKR"}) · Konten: {testResult.accounts?.join(", ") || "—"}
            {testResult.trading_enabled ? " · Trading AKTIV ⚠️" : " · read-only"}
          </div>
          <div className="flex flex-wrap gap-4 text-slate-300">
            {Object.entries(testResult.summary || {}).filter(([, v]: any) => v?.value != null).map(([tag, v]: any) => (
              <span key={tag}>{tag}: <b>{Number(v.value).toLocaleString("de-DE")} {v.currency || ""}</b></span>
            ))}
          </div>
          {testResult.positions?.length > 0 && (
            <div className="mt-1 text-slate-400">
              IBKR-Positionen: {testResult.positions.map((p: any) => `${p.symbol}×${p.quantity}`).join(", ")}
            </div>
          )}
        </div>
      )}
    </section>
  );
}

/* ------------------------------------------------------------------ Reddit */

function RedditSection() {
  const [clientId, setClientId] = useState("");
  const [secret, setSecret] = useState("");
  const [hasSecret, setHasSecret] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    api.get("/api/settings/reddit").then((d) => {
      setClientId(d.client_id || "");
      setHasSecret(!!d.has_client_secret);
    }).catch(() => {});
  }, []);

  async function save() {
    setMsg(null);
    try {
      const d = await api.put("/api/settings/reddit", {
        client_id: clientId, client_secret: secret || undefined,
      });
      setHasSecret(!!d.has_client_secret);
      setSecret("");
      setMsg("✅ Gespeichert — Reddit-Quellen laufen ab dem nächsten News-Sync über die API.");
    } catch (e: any) {
      setMsg(e.message);
    }
  }

  return (
    <section className="rounded-lg border border-slate-800 bg-slate-900/50 p-4">
      <h2 className="mb-1 font-semibold">👽 Reddit-API</h2>
      <p className="mb-3 text-xs text-slate-500">
        Reddit filtert automatisierte RSS-Abrufe (429). Mit einer kostenlosen API-App laufen die
        r/-Quellen stabil über OAuth: auf <code>reddit.com/prefs/apps</code> eine App vom Typ{" "}
        <b>„script"</b> anlegen — die ID steht unter dem App-Namen, das Secret daneben.
      </p>
      <FieldGrid cols={2}>
        <Field label="Client-ID">
          <input value={clientId} onChange={(e) => setClientId(e.target.value)} className={inputCls} />
        </Field>
        <Field label={`Client-Secret${hasSecret ? " (gesetzt)" : ""}`}>
          <input type="password" value={secret} onChange={(e) => setSecret(e.target.value)}
            placeholder={hasSecret ? "unverändert lassen" : ""} className={inputCls} />
        </Field>
      </FieldGrid>
      <div className="mt-3 flex items-center gap-3">
        <button onClick={save} className="rounded bg-sky-600 px-4 py-2 text-sm font-semibold hover:bg-sky-500">
          Speichern
        </button>
        {msg && <span className="text-xs text-amber-400">{msg}</span>}
      </div>
    </section>
  );
}

/* ------------------------------------------------------- Handelsplattformen */

type FeeTier = {
  up_to: number | null;
  fee?: number;        // Flat
  pct?: number;        // Anteil vom Volumen (0.0005 = 0,05 %)
  per_share?: number;  // pro Aktie (IBKR US)
  min?: number; max?: number; max_pct?: number;
};

function tierModel(t: FeeTier): "fee" | "pct" | "per_share" {
  if (t.pct !== undefined && t.pct !== null) return "pct";
  if (t.per_share !== undefined && t.per_share !== null) return "per_share";
  return "fee";
}
type Platform = { id: number; name: string; fees: Record<string, FeeTier[]> };

function PlatformsSection() {
  const [platforms, setPlatforms] = useState<Platform[]>([]);
  const [msg, setMsg] = useState<string | null>(null);

  const load = useCallback(() => {
    api.get("/api/platforms").then(setPlatforms).catch(() => {});
  }, []);
  useEffect(load, [load]);

  async function addPlatform() {
    const name = window.prompt("Name der Plattform (z.B. Interactive Brokers)?");
    if (!name?.trim()) return;
    await api.post("/api/platforms", {
      name: name.trim(),
      fees: { default: [{ up_to: null, fee: 0 }] },
    });
    load();
  }

  return (
    <section className="rounded-lg border border-slate-800 bg-slate-900/50 p-4">
      <div className="mb-1 flex items-center gap-3">
        <h2 className="font-semibold">💰 Handelsplattformen (Gebühren)</h2>
        <button onClick={addPlatform}
          className="ml-auto rounded border border-slate-700 px-3 py-1 text-xs text-slate-300 hover:border-sky-500">
          + Plattform
        </button>
      </div>
      <p className="mb-3 text-xs text-slate-500">
        Gebührenstaffeln je Transaktionsvolumen; Käufe/Verkäufe (auch Auto-Portfolio) buchen die
        Gebühr automatisch ins P/L. Portfolios wählen ihre Plattform auf der Portfolios-Seite.
        Staffeln gelten je Währungsgruppe — „default" greift, wenn keine passende Währung definiert ist.
      </p>
      {msg && <p className="mb-2 text-sm text-amber-400">{msg}</p>}
      <div className="space-y-3">
        {platforms.map((p) => (
          <PlatformEditor key={p.id} platform={p} onChanged={load} onMsg={setMsg} />
        ))}
      </div>
    </section>
  );
}

function PlatformEditor({ platform, onChanged, onMsg }: {
  platform: Platform; onChanged: () => void; onMsg: (m: string) => void;
}) {
  const [name, setName] = useState(platform.name);
  const [fees, setFees] = useState<Record<string, FeeTier[]>>(platform.fees || {});
  const groups = Object.keys(fees);
  const [group, setGroup] = useState(groups[0] || "default");
  const tiers = fees[group] || [];

  function patchTier(i: number, patch: Partial<FeeTier>) {
    setFees({ ...fees, [group]: tiers.map((t, idx) => idx === i ? { ...t, ...patch } : t) });
  }

  function setNum(i: number, field: keyof FeeTier, raw: string, scale = 1) {
    const v = raw === "" ? undefined : parseFloat(raw.replace(",", ".")) / scale;
    patchTier(i, { [field]: field === "up_to" && v === undefined ? null : v } as Partial<FeeTier>);
  }

  function setModel(i: number, model: "fee" | "pct" | "per_share") {
    const t = tiers[i];
    const value = t.fee ?? t.pct ?? t.per_share ?? 0;
    patchTier(i, { fee: undefined, pct: undefined, per_share: undefined, [model]: value });
  }

  async function save() {
    try {
      await api.put(`/api/platforms/${platform.id}`, { name: name.trim(), fees });
      onMsg(`✅ ${name} gespeichert.`);
      onChanged();
    } catch (e: any) {
      onMsg(`❌ ${e.message}`);
    }
  }

  async function remove() {
    if (!confirm(`Plattform "${platform.name}" löschen? Portfolios verlieren ihre Gebührenzuordnung.`)) return;
    await api.del(`/api/platforms/${platform.id}`);
    onChanged();
  }

  function addGroup() {
    const code = window.prompt("Währungscode für eigene Staffel (z.B. EUR, GBP)?");
    if (!code?.trim()) return;
    const key = code.trim().toUpperCase();
    setFees({ ...fees, [key]: fees[key] || [{ up_to: null, fee: 0 }] });
    setGroup(key);
  }

  return (
    <div className="rounded border border-slate-800 bg-slate-900/40 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <input value={name} onChange={(e) => setName(e.target.value)}
          className="w-56 rounded border border-slate-700 bg-slate-900 px-2 py-1 text-sm font-semibold" />
        <div className="flex items-center gap-1">
          {groups.map((g) => (
            <button key={g} onClick={() => setGroup(g)}
              className={`rounded-full border px-2 py-0.5 text-xs ${g === group ? "border-sky-500 text-sky-400" : "border-slate-700 text-slate-400"}`}>
              {g}
            </button>
          ))}
          <button onClick={addGroup} title="Währungsgruppe mit eigener Staffel ergänzen"
            className="rounded-full border border-slate-700 px-2 py-0.5 text-xs text-slate-500 hover:border-slate-500">+</button>
        </div>
        <button onClick={save} className="ml-auto rounded bg-sky-600 px-3 py-1 text-xs font-semibold hover:bg-sky-500">
          Speichern
        </button>
        <button onClick={remove} className="rounded border border-slate-700 px-2 py-1 text-xs text-rose-400 hover:border-rose-500">
          Löschen
        </button>
      </div>
      <table className="mt-2 text-xs">
        <thead className="text-slate-500">
          <tr>
            <th className="pr-3 text-left">Volumen bis (leer = darüber)</th>
            <th className="pr-3 text-left">Modell</th>
            <th className="pr-3 text-left">Satz</th>
            <th className="pr-3 text-left">Min</th>
            <th className="pr-3 text-left">Max</th>
            <th className="pr-3 text-left" title="Deckel als % vom Volumen (z.B. IBKR US: 1%)">Max %</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {tiers.map((t, i) => {
            const model = tierModel(t);
            const inputCls = "rounded border border-slate-700 bg-slate-900 px-2 py-1";
            return (
              <tr key={i}>
                <td className="pr-3 py-0.5">
                  <input value={t.up_to ?? ""} onChange={(e) => setNum(i, "up_to", e.target.value)}
                    placeholder="∞" className={`w-24 ${inputCls}`} />
                </td>
                <td className="pr-3 py-0.5">
                  <select value={model} onChange={(e) => setModel(i, e.target.value as any)}
                    className={inputCls}>
                    <option value="fee">Flat</option>
                    <option value="pct">% vom Volumen</option>
                    <option value="per_share">pro Aktie</option>
                  </select>
                </td>
                <td className="pr-3 py-0.5">
                  <input
                    value={model === "pct"
                      ? (t.pct !== undefined ? +(t.pct * 100).toFixed(6) : "")
                      : (t[model] ?? "")}
                    onChange={(e) => setNum(i, model, e.target.value, model === "pct" ? 100 : 1)}
                    className={`w-20 ${inputCls}`} />
                  {model === "pct" && <span className="ml-1 text-slate-500">%</span>}
                </td>
                <td className="pr-3 py-0.5">
                  <input value={t.min ?? ""} onChange={(e) => setNum(i, "min", e.target.value)}
                    placeholder="—" className={`w-16 ${inputCls}`} />
                </td>
                <td className="pr-3 py-0.5">
                  <input value={t.max ?? ""} onChange={(e) => setNum(i, "max", e.target.value)}
                    placeholder="—" className={`w-16 ${inputCls}`} />
                </td>
                <td className="pr-3 py-0.5">
                  <input value={t.max_pct !== undefined ? +(t.max_pct * 100).toFixed(4) : ""}
                    onChange={(e) => setNum(i, "max_pct", e.target.value, 100)}
                    placeholder="—" className={`w-16 ${inputCls}`} />
                </td>
                <td>
                  <button onClick={() => setFees({ ...fees, [group]: tiers.filter((_, idx) => idx !== i) })}
                    className="text-rose-400 hover:underline">✕</button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <button onClick={() => setFees({ ...fees, [group]: [...tiers, { up_to: null, fee: 0 }] })}
        className="mt-1 rounded border border-slate-700 px-2 py-0.5 text-xs text-slate-400 hover:border-slate-500">
        + Stufe
      </button>
    </div>
  );
}

/* --------------------------------------------------------------- MCP */

function McpSection() {
  const [token, setToken] = useState<string | null>(null);
  const [origin, setOrigin] = useState("");
  const [copied, setCopied] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    setOrigin(window.location.origin);
    api.get("/api/settings/mcp").then((d) => setToken(d.token)).catch(() => {});
  }, []);

  async function generate() {
    if (token && !confirm("Neues Token erzeugen? Bestehende MCP-Adapter verlieren sofort den Zugriff.")) return;
    try {
      const res = await api.post("/api/settings/mcp/generate");
      setToken(res.token);
      setMsg("Neues Token aktiv.");
    } catch (e: any) {
      setMsg(e.message);
    }
  }

  async function copy(label: string, text: string) {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(label);
      setTimeout(() => setCopied(null), 2000);
    } catch {
      setMsg("Kopieren fehlgeschlagen — bitte manuell markieren.");
    }
  }

  const url = `${origin}/api/mcp`;
  const tok = token ?? "<TOKEN>";
  const claudeCmd = `claude mcp add --transport http stx ${url} --header "x-stx-token: ${tok}"`;
  const remoteJson = JSON.stringify(
    {
      mcpServers: {
        stx: {
          command: "npx",
          args: ["mcp-remote", url, "--header", `x-stx-token: ${tok}`],
        },
      },
    },
    null,
    2
  );

  return (
    <section className="rounded-lg border border-slate-800 bg-slate-900/50 p-4">
      <h2 className="mb-1 font-semibold">🔌 MCP-Connector</h2>
      <p className="mb-3 text-xs text-slate-500">
        Externe LLM-Agenten (curai, Claude, …) greifen per MCP auf Signale, Screener, Portfolios
        und Analysen zu. Auth über Token-Header — ohne Token ist der Endpunkt deaktiviert.
      </p>

      <div className="flex flex-wrap items-center gap-3">
        <div className="flex flex-col gap-1 text-xs text-slate-400">
          Token
          <div className="flex items-center gap-2">
            <code className="rounded border border-slate-700 bg-slate-900 px-3 py-2 text-sm">
              {token ?? "— nicht gesetzt —"}
            </code>
            {token && (
              <CopyBtn label="token" copied={copied} onClick={() => copy("token", token)} />
            )}
          </div>
        </div>
        <button onClick={generate}
          className="self-end rounded bg-sky-600 px-4 py-2 text-sm font-semibold hover:bg-sky-500">
          {token ? "Token neu generieren" : "Token generieren"}
        </button>
        <ResetButton section="mcp" onDone={() => api.get("/api/settings/mcp").then((d) => setToken(d.token))} />
        {msg && <span className="self-end text-sm text-amber-400">{msg}</span>}
      </div>

      <div className="mt-4 space-y-3">
        <Snippet label="Endpoint" text={url} copied={copied} onCopy={copy} />
        <Snippet label="Claude Code" text={claudeCmd} copied={copied} onCopy={copy} />
        <Snippet label="mcp-remote (JSON-Config für Adapter ohne Streamable-HTTP)" text={remoteJson} copied={copied} onCopy={copy} />
      </div>
    </section>
  );
}

function Snippet({ label, text, copied, onCopy }: {
  label: string; text: string; copied: string | null;
  onCopy: (label: string, text: string) => void;
}) {
  return (
    <div>
      <div className="mb-1 flex items-center gap-2 text-xs text-slate-400">
        {label}
        <CopyBtn label={label} copied={copied} onClick={() => onCopy(label, text)} />
      </div>
      <pre className="overflow-x-auto rounded border border-slate-800 bg-slate-900 p-3 text-xs leading-relaxed">
        {text}
      </pre>
    </div>
  );
}

function CopyBtn({ label, copied, onClick }: { label: string; copied: string | null; onClick: () => void }) {
  return (
    <button onClick={onClick}
      className="rounded border border-slate-700 px-2 py-0.5 text-xs text-slate-400 hover:border-sky-500 hover:text-sky-400">
      {copied === label ? "✅ kopiert" : "📋 Copy"}
    </button>
  );
}

/* ------------------------------------------------------------------ LLM */

function LlmSection({ initial, onSaved }: { initial: LlmView; onSaved: () => void }) {
  const [provider, setProvider] = useState(initial.provider);
  const [baseUrl, setBaseUrl] = useState(initial.base_url);
  const [model, setModel] = useState(initial.model);
  const [reasoningMode, setReasoningMode] = useState(initial.reasoning_mode || "none");
  const [apiKey, setApiKey] = useState("");
  const [models, setModels] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  // Ungespeicherte Änderungen? Test/Modell-Fetch nutzen Formularwerte,
  // die Pipeline aber nur GESPEICHERTE — der Hinweis verhindert, dass
  // ein erfolgreicher Test ohne Speichern für "eingerichtet" gehalten wird.
  const dirty =
    provider !== initial.provider || baseUrl !== initial.base_url ||
    model !== initial.model || reasoningMode !== (initial.reasoning_mode || "none") ||
    apiKey !== "";
  const unsavedHint = dirty ? " ⚠️ Noch nicht gespeichert — die Analyse nutzt nur gespeicherte Werte!" : "";

  async function fetchModels() {
    setBusy(true);
    setMsg(null);
    try {
      const res = await api.post("/api/settings/llm/models", {
        provider, base_url: baseUrl, api_key: apiKey || null,
      });
      setModels(res.models);
      setMsg(`${res.models.length} Modelle gefunden — Verbindung OK.${unsavedHint}`);
    } catch (e: any) {
      setMsg(e.message);
      setModels([]);
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    setMsg(null);
    try {
      await api.put("/api/settings/llm", {
        provider, base_url: baseUrl, model, reasoning_mode: reasoningMode,
        api_key: apiKey || null,
      });
      setApiKey("");
      setMsg("Gespeichert.");
      onSaved();
    } catch (e: any) {
      setMsg(e.message);
    }
  }

  async function testLlm() {
    setBusy(true);
    setMsg(null);
    try {
      const res = await api.post("/api/settings/llm/test", {
        provider, base_url: baseUrl, model, reasoning_mode: reasoningMode,
        api_key: apiKey || null,
      });
      setMsg(`✅ ${res.model} antwortet in ${res.latency_ms} ms: „${res.reply}"${unsavedHint}`);
    } catch (e: any) {
      setMsg(`❌ ${e.message}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="rounded-lg border border-slate-800 bg-slate-900/50 p-4">
      <h2 className="mb-3 font-semibold">🤖 LLM</h2>
      <FieldGrid>
        <Field label="Provider">
          <select value={provider} onChange={(e) => setProvider(e.target.value)} className={inputCls}>
            <option value="openai">OpenAI-kompatibel</option>
            <option value="anthropic">Anthropic</option>
          </select>
        </Field>
        <Field label="Base-URL (vLLM, Ollama, OpenRouter, …)" className="lg:col-span-2">
          <input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} className={inputCls} />
        </Field>
        <Field label="Thinking/Reasoning">
          <select value={reasoningMode} onChange={(e) => setReasoningMode(e.target.value)}
            className={inputCls}
            title="Reasoning-Modelle denken sonst exzessiv — kostet Latenz und Tokens">
            <option value="none">Nicht steuern (Modell entscheidet)</option>
            <option value="qwen_template">Aus — Qwen3/3.5/3.6 auf vLLM</option>
            <option value="openai_effort">Minimal — OpenAI o-Serie/GPT-5</option>
            <option value="disable_field">Aus — MiniMax-Stil (disable_thinking)</option>
          </select>
        </Field>
        <Field label={`API-Key ${initial.has_api_key ? "(gespeichert — leer = behalten)" : ""}`}>
          <input type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)}
            placeholder={initial.has_api_key ? "••••••••" : "sk-…"} className={inputCls} />
        </Field>
        <Field label="Modell" className="lg:col-span-2">
          <div className="flex gap-2">
            <input value={model} onChange={(e) => setModel(e.target.value)} list="llm-models"
              className={inputCls} />
            <datalist id="llm-models">
              {models.map((m) => <option key={m} value={m} />)}
            </datalist>
            <button onClick={fetchModels} disabled={busy}
              className="shrink-0 rounded border border-slate-700 px-3 py-2 text-sm hover:border-sky-500 disabled:opacity-50">
              {busy ? "Lade…" : "Modelle laden"}
            </button>
          </div>
        </Field>
      </FieldGrid>
      {models.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1">
          {models.slice(0, 20).map((m) => (
            <button key={m} onClick={() => setModel(m)}
              className={`rounded-full border px-2 py-0.5 text-xs ${m === model ? "border-sky-500 text-sky-400" : "border-slate-700 text-slate-400 hover:border-slate-500"}`}>
              {m}
            </button>
          ))}
          {models.length > 20 && <span className="text-xs text-slate-500">… +{models.length - 20} weitere (Tippen im Feld filtert)</span>}
        </div>
      )}
      <div className="mt-3 flex items-center gap-3">
        <button onClick={save} className="rounded bg-sky-600 px-4 py-2 text-sm font-semibold hover:bg-sky-500">
          Speichern
        </button>
        <button onClick={testLlm} disabled={busy}
          className="rounded border border-slate-700 px-3 py-2 text-sm hover:border-emerald-500 disabled:opacity-50">
          {busy ? "Teste…" : "Verbindung testen"}
        </button>
        <ResetButton section="llm" onDone={onSaved} />
        {msg && <span className="text-sm text-amber-400">{msg}</span>}
      </div>
    </section>
  );
}

function ResetButton({ section, onDone }: { section: string; onDone: () => void }) {
  return (
    <button
      onClick={async () => {
        if (!confirm("Alle Overrides dieser Sektion löschen und auf .env-Defaults zurücksetzen?")) return;
        await api.del(`/api/settings/${section}`);
        onDone();
      }}
      className="rounded border border-slate-700 px-3 py-2 text-sm text-slate-400 hover:border-rose-500 hover:text-rose-400"
    >
      Auf .env zurücksetzen
    </button>
  );
}

/* ----------------------------------------------------------- Kommunikation */

function CommSection({ initial, onSaved }: { initial: CommView; onSaved: () => void }) {
  const [form, setForm] = useState({
    smtp_host: initial.smtp_host || "",
    smtp_port: String(initial.smtp_port || "587"),
    smtp_user: initial.smtp_user || "",
    smtp_from: initial.smtp_from || "",
    alert_email_to: initial.alert_email_to || "",
    smtp_password: "",
    telegram_chat_id: initial.telegram_chat_id || "",
    telegram_bot_token: "",
  });
  const [msg, setMsg] = useState<string | null>(null);

  function set(k: string, v: string) {
    setForm((f) => ({ ...f, [k]: v }));
  }

  async function save() {
    setMsg(null);
    try {
      await api.put("/api/settings/comm", {
        ...form,
        smtp_password: form.smtp_password || null,
        telegram_bot_token: form.telegram_bot_token || null,
      });
      setForm((f) => ({ ...f, smtp_password: "", telegram_bot_token: "" }));
      setMsg("Gespeichert.");
      onSaved();
    } catch (e: any) {
      setMsg(e.message);
    }
  }

  async function test(channel: "email" | "telegram") {
    setMsg(null);
    try {
      await api.post("/api/settings/comm/test", {
        ...form, channel,
        smtp_password: form.smtp_password || null,
        telegram_bot_token: form.telegram_bot_token || null,
      });
      setMsg(`✅ Testnachricht via ${channel === "email" ? "E-Mail" : "Telegram"} versendet — bitte Eingang prüfen.`);
    } catch (e: any) {
      setMsg(`❌ ${e.message}`);
    }
  }

  return (
    <section className="rounded-lg border border-slate-800 bg-slate-900/50 p-4">
      <h2 className="mb-3 font-semibold">📨 Kommunikation (Alerts)</h2>
      <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">E-Mail (SMTP)</div>
      <FieldGrid>
        <Field label="SMTP-Host"><input value={form.smtp_host} onChange={(e) => set("smtp_host", e.target.value)} className={inputCls} /></Field>
        <Field label="Port"><input value={form.smtp_port} onChange={(e) => set("smtp_port", e.target.value)} className={inputCls} /></Field>
        <Field label="Benutzer"><input value={form.smtp_user} onChange={(e) => set("smtp_user", e.target.value)} className={inputCls} /></Field>
        <Field label={`Passwort ${initial.has_smtp_password ? "(gespeichert)" : ""}`}>
          <input type="password" value={form.smtp_password} onChange={(e) => set("smtp_password", e.target.value)}
            placeholder={initial.has_smtp_password ? "••••••••" : ""} className={inputCls} />
        </Field>
        <Field label="Absender (From)"><input value={form.smtp_from} onChange={(e) => set("smtp_from", e.target.value)} className={inputCls} /></Field>
        <Field label="Alert-Empfänger"><input value={form.alert_email_to} onChange={(e) => set("alert_email_to", e.target.value)} className={inputCls} /></Field>
      </FieldGrid>
      <div className="mb-2 mt-4 text-xs font-semibold uppercase tracking-wide text-slate-500">Telegram</div>
      <FieldGrid cols={2}>
        <Field label={`Bot-Token ${initial.has_telegram_bot_token ? "(gespeichert)" : ""}`}>
          <input type="password" value={form.telegram_bot_token} onChange={(e) => set("telegram_bot_token", e.target.value)}
            placeholder={initial.has_telegram_bot_token ? "••••••••" : "123456:ABC…"} className={inputCls} />
        </Field>
        <Field label="Chat-ID"><input value={form.telegram_chat_id} onChange={(e) => set("telegram_chat_id", e.target.value)} className={inputCls} /></Field>
      </FieldGrid>
      <div className="mt-3 flex items-center gap-3">
        <button onClick={save} className="rounded bg-sky-600 px-4 py-2 text-sm font-semibold hover:bg-sky-500">
          Speichern
        </button>
        <button onClick={() => test("email")}
          className="rounded border border-slate-700 px-3 py-2 text-sm hover:border-emerald-500">
          Test-E-Mail
        </button>
        <button onClick={() => test("telegram")}
          className="rounded border border-slate-700 px-3 py-2 text-sm hover:border-emerald-500">
          Test-Telegram
        </button>
        <ResetButton section="comm" onDone={onSaved} />
        {msg && <span className="text-sm text-amber-400">{msg}</span>}
      </div>
    </section>
  );
}

/* ------------------------------------------------------------ Datenquellen */

type Source = {
  id: number; kind: string; name: string; url: string | null; enabled: boolean;
  priority: number; last_fetch_at: string | null; last_error: string | null;
};

function SourcesSection() {
  const [sources, setSources] = useState<Source[]>([]);
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api.get("/api/sources").then(setSources).catch((e) => setError(e.message));
  }, []);
  useEffect(load, [load]);

  async function add(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim() || !url.trim()) return;
    try {
      await api.post("/api/sources", { name: name.trim(), url: url.trim() });
      setName("");
      setUrl("");
      load();
    } catch (err: any) {
      setError(err.message);
    }
  }

  async function toggle(s: Source) {
    await api.patch(`/api/sources/${s.id}`, { enabled: !s.enabled });
    load();
  }

  async function remove(s: Source) {
    if (!confirm(`Quelle "${s.name}" löschen?`)) return;
    await api.del(`/api/sources/${s.id}`);
    load();
  }

  return (
    <section className="rounded-lg border border-slate-800 bg-slate-900/50 p-4">
      <h2 className="mb-3 font-semibold">📰 Datenquellen (News-RSS)</h2>
      <form onSubmit={add} className="mb-3 flex flex-wrap gap-2">
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Name" className={inputCls + " w-56"} />
        <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="RSS-Feed-URL" className={inputCls + " w-96"} />
        <button className="rounded bg-sky-600 px-4 py-2 text-sm font-semibold hover:bg-sky-500">Hinzufügen</button>
      </form>
      {error && <p className="mb-2 text-sm text-rose-400">{error}</p>}
      <div className="space-y-2">
        {sources.map((s) => (
          <div key={s.id} className="flex items-center gap-3 rounded border border-slate-800 bg-slate-900/40 p-3 text-sm">
            <button
              onClick={() => toggle(s)}
              className={`rounded border px-2 py-1 text-xs ${s.enabled ? "border-emerald-700 text-emerald-400" : "border-slate-700 text-slate-500"}`}
            >
              {s.enabled ? "aktiv" : "aus"}
            </button>
            <div className="min-w-0 flex-1">
              <div className="font-medium">{s.name}</div>
              <div className="truncate text-xs text-slate-500">{s.url}</div>
              {s.last_error && <div className="text-xs text-rose-400">Fehler: {s.last_error}</div>}
            </div>
            <div className="text-xs text-slate-500">
              {s.last_fetch_at ? `Zuletzt: ${new Date(s.last_fetch_at).toLocaleString("de-DE")}` : "noch nie"}
            </div>
            <button onClick={() => remove(s)} className="text-xs text-rose-400 hover:underline">Löschen</button>
          </div>
        ))}
      </div>
    </section>
  );
}

/* ----------------------------------------------------------------- Helfer */

const inputCls =
  "w-full rounded border border-slate-700 bg-slate-900 px-3 py-2 text-sm outline-none focus:border-sky-500";

/** Beschriftetes Feld; erzwingt volle Breite auf direkten Inputs/Selects,
 *  damit alle Felder im Grid sauber in Spalten fluchten. */
function Field({ label, children, className = "" }: {
  label: string; children: React.ReactNode; className?: string;
}) {
  return (
    <label className={`flex min-w-0 flex-col gap-1 text-xs text-slate-400 [&>input]:w-full [&>select]:w-full ${className}`}>
      <span className="truncate" title={label}>{label}</span>
      {children}
    </label>
  );
}

/** Responsives Formular-Raster — Felder richten sich in gleich breiten
 *  Spalten aus statt beim Umbruch zu zerfransen. */
function FieldGrid({ children, cols = 3 }: { children: React.ReactNode; cols?: 2 | 3 }) {
  const lg = cols === 2 ? "sm:grid-cols-2" : "sm:grid-cols-2 lg:grid-cols-3";
  return <div className={`grid grid-cols-1 gap-x-4 gap-y-3 ${lg}`}>{children}</div>;
}
