"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";

type LlmView = { provider: string; base_url: string; model: string; has_api_key: boolean };
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
      <SourcesSection />
    </div>
  );
}

/* ------------------------------------------------------------------ LLM */

function LlmSection({ initial, onSaved }: { initial: LlmView; onSaved: () => void }) {
  const [provider, setProvider] = useState(initial.provider);
  const [baseUrl, setBaseUrl] = useState(initial.base_url);
  const [model, setModel] = useState(initial.model);
  const [apiKey, setApiKey] = useState("");
  const [models, setModels] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  async function fetchModels() {
    setBusy(true);
    setMsg(null);
    try {
      const res = await api.post("/api/settings/llm/models", {
        provider, base_url: baseUrl, api_key: apiKey || null,
      });
      setModels(res.models);
      setMsg(`${res.models.length} Modelle gefunden — Verbindung OK.`);
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
        provider, base_url: baseUrl, model, api_key: apiKey || null,
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
        provider, base_url: baseUrl, model, api_key: apiKey || null,
      });
      setMsg(`✅ ${res.model} antwortet in ${res.latency_ms} ms: „${res.reply}"`);
    } catch (e: any) {
      setMsg(`❌ ${e.message}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="rounded-lg border border-slate-800 bg-slate-900/50 p-4">
      <h2 className="mb-3 font-semibold">🤖 LLM</h2>
      <div className="flex flex-wrap gap-3">
        <Field label="Provider">
          <select value={provider} onChange={(e) => setProvider(e.target.value)} className={inputCls + " w-44"}>
            <option value="openai">OpenAI-kompatibel</option>
            <option value="anthropic">Anthropic</option>
          </select>
        </Field>
        <Field label="Base-URL (vLLM, Ollama, OpenRouter, …)">
          <input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} className={inputCls + " w-96"} />
        </Field>
        <Field label={`API-Key ${initial.has_api_key ? "(gespeichert — leer lassen zum Behalten)" : ""}`}>
          <input type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)}
            placeholder={initial.has_api_key ? "••••••••" : "sk-…"} className={inputCls + " w-72"} />
        </Field>
        <Field label="Modell">
          <div className="flex gap-2">
            <input value={model} onChange={(e) => setModel(e.target.value)} list="llm-models"
              className={inputCls + " w-72"} />
            <datalist id="llm-models">
              {models.map((m) => <option key={m} value={m} />)}
            </datalist>
            <button onClick={fetchModels} disabled={busy}
              className="rounded border border-slate-700 px-3 py-2 text-sm hover:border-sky-500 disabled:opacity-50">
              {busy ? "Lade…" : "Modelle laden"}
            </button>
          </div>
        </Field>
      </div>
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
      <div className="mb-2 text-sm text-slate-400">E-Mail (SMTP)</div>
      <div className="flex flex-wrap gap-3">
        <Field label="SMTP-Host"><input value={form.smtp_host} onChange={(e) => set("smtp_host", e.target.value)} className={inputCls + " w-64"} /></Field>
        <Field label="Port"><input value={form.smtp_port} onChange={(e) => set("smtp_port", e.target.value)} className={inputCls + " w-20"} /></Field>
        <Field label="Benutzer"><input value={form.smtp_user} onChange={(e) => set("smtp_user", e.target.value)} className={inputCls + " w-64"} /></Field>
        <Field label={`Passwort ${initial.has_smtp_password ? "(gespeichert)" : ""}`}>
          <input type="password" value={form.smtp_password} onChange={(e) => set("smtp_password", e.target.value)}
            placeholder={initial.has_smtp_password ? "••••••••" : ""} className={inputCls + " w-52"} />
        </Field>
        <Field label="Absender (From)"><input value={form.smtp_from} onChange={(e) => set("smtp_from", e.target.value)} className={inputCls + " w-64"} /></Field>
        <Field label="Alert-Empfänger"><input value={form.alert_email_to} onChange={(e) => set("alert_email_to", e.target.value)} className={inputCls + " w-64"} /></Field>
      </div>
      <div className="mb-2 mt-4 text-sm text-slate-400">Telegram</div>
      <div className="flex flex-wrap gap-3">
        <Field label={`Bot-Token ${initial.has_telegram_bot_token ? "(gespeichert)" : ""}`}>
          <input type="password" value={form.telegram_bot_token} onChange={(e) => set("telegram_bot_token", e.target.value)}
            placeholder={initial.has_telegram_bot_token ? "••••••••" : "123456:ABC…"} className={inputCls + " w-72"} />
        </Field>
        <Field label="Chat-ID"><input value={form.telegram_chat_id} onChange={(e) => set("telegram_chat_id", e.target.value)} className={inputCls + " w-44"} /></Field>
      </div>
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
  "rounded border border-slate-700 bg-slate-900 px-3 py-2 text-sm outline-none focus:border-sky-500";

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1 text-xs text-slate-400">
      {label}
      {children}
    </label>
  );
}
