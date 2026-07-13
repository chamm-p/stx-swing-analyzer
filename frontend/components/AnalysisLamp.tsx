/** Analyse-Frische als Lampe: grün ≤2h, gelb ≤24h, rot älter, grau nie. */
export default function AnalysisLamp({ ts }: { ts: string | null | undefined }) {
  let color = "bg-slate-600/60";
  let label = "Noch nie analysiert";
  if (ts) {
    const ageH = (Date.now() - new Date(ts).getTime()) / 3600000;
    if (ageH <= 2) {
      color = "bg-emerald-400";
      label = `Analyse aktuell (${new Date(ts).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" })})`;
    } else if (ageH <= 24) {
      color = "bg-amber-400";
      label = `Analyse ${Math.round(ageH)}h alt`;
    } else {
      color = "bg-rose-500";
      label = `Analyse veraltet (${new Date(ts).toLocaleDateString("de-DE")})`;
    }
  }
  return (
    <span
      title={label}
      className={`inline-block h-2.5 w-2.5 shrink-0 rounded-full ${color}`}
      aria-label={label}
    />
  );
}
