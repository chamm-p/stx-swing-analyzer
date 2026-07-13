"use client";

import { useEffect, useState } from "react";

type Me = { email: string; name: string | null; auth_mode: string };

export default function UserMenu() {
  const [me, setMe] = useState<Me | null>(null);

  useEffect(() => {
    // Bewusst plain fetch (ohne 401-Redirect-Handler): auf /logged-out
    // darf ein 401 NICHT automatisch zum IdP weiterleiten.
    fetch("/api/auth/me")
      .then((r) => (r.ok ? r.json() : null))
      .then(setMe)
      .catch(() => {});
  }, []);

  // Im Dev-Modus (AUTH_MODE=none) gibt es nichts abzumelden
  if (!me || me.auth_mode !== "oidc") return null;

  return (
    <div className="flex items-center gap-2">
      <span className="hidden text-xs text-slate-500 sm:block" title={me.email}>
        {me.name || me.email}
      </span>
      <a
        href="/api/auth/logout"
        title={`Abmelden (${me.email})`}
        className="rounded border border-slate-700 px-2.5 py-1 text-xs text-slate-300 hover:border-rose-500 hover:text-rose-400"
      >
        Abmelden
      </a>
    </div>
  );
}
