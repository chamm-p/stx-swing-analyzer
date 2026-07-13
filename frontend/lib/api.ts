export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function handle(res: Response) {
  if (res.status === 401) {
    // Nicht angemeldet → OIDC-Login starten
    window.location.href = "/api/auth/login";
    throw new ApiError(401, "Nicht angemeldet");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch {}
    throw new ApiError(res.status, detail);
  }
  return res.json();
}

export const api = {
  get: (path: string) => fetch(path, { cache: "no-store" }).then(handle),
  post: (path: string, body?: unknown) =>
    fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    }).then(handle),
  put: (path: string, body: unknown) =>
    fetch(path, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(handle),
  patch: (path: string, body: unknown) =>
    fetch(path, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(handle),
  del: (path: string) => fetch(path, { method: "DELETE" }).then(handle),
};

export type Signal = {
  id: string;
  symbol: string;
  ts: string;
  action: "BUY" | "SELL" | "HOLD";
  confidence: number;
  composite_score: number;
  technical_score: number | null;
  sentiment_score: number | null;
  fundamental_score: number | null;
  rationale: string | null;
  horizon_days: number;
  price_at_signal: number | null;
};

export type WatchlistEntry = {
  symbol: string;
  name: string | null;
  asset_type: string;
  currency: string | null;
  alert_enabled: boolean;
  min_confidence: number;
  notes: string | null;
  last_signal: Signal | null;
  source: "watchlist" | "portfolio";
  portfolios?: string[];
};
