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
        aria-label="Abmelden"
        className="rounded-full border border-slate-700 p-1.5 text-slate-400 hover:border-rose-500 hover:text-rose-400"
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
          strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
          <polyline points="16 17 21 12 16 7" />
          <line x1="21" y1="12" x2="9" y2="12" />
        </svg>
      </a>
    </div>
  );
}
