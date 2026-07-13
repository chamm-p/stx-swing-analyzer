// Bewusst ohne API-Aufrufe: Nach dem Logout darf nichts automatisch
// zurück zum IdP-Login leiten.
export default function LoggedOutPage() {
  return (
    <div className="flex flex-col items-center gap-4 py-24 text-center">
      <div className="text-4xl">👋</div>
      <h1 className="text-xl font-bold">Abgemeldet</h1>
      <p className="text-sm text-slate-400">
        Deine Sitzung wurde beendet — auch beim SSO-Provider.
      </p>
      <a
        href="/api/auth/login"
        className="rounded bg-sky-600 px-4 py-2 text-sm font-semibold text-white hover:bg-sky-500"
      >
        Erneut anmelden
      </a>
    </div>
  );
}
