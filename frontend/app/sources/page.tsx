"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";

type Source = {
  id: number; kind: string; name: string; url: string | null; enabled: boolean;
  priority: number; last_fetch_at: string | null; last_error: string | null;
};

export default function SourcesPage() {
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
    <div className="space-y-6">
      <h1 className="text-xl font-bold">Datenquellen (News-RSS)</h1>

      <form onSubmit={add} className="flex flex-wrap gap-2">
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Name"
          className="w-56 rounded border border-slate-700 bg-slate-900 px-3 py-2 text-sm outline-none focus:border-sky-500" />
        <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="RSS-Feed-URL"
          className="w-96 rounded border border-slate-700 bg-slate-900 px-3 py-2 text-sm outline-none focus:border-sky-500" />
        <button className="rounded bg-sky-600 px-4 py-2 text-sm font-semibold hover:bg-sky-500">Hinzufügen</button>
      </form>
      {error && <p className="text-sm text-rose-400">{error}</p>}

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
    </div>
  );
}
