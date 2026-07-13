export default function SignalBadge({ action, confidence }: { action: string; confidence?: number }) {
  const styles: Record<string, string> = {
    BUY: "bg-emerald-600/20 text-emerald-400 border-emerald-600",
    SELL: "bg-rose-600/20 text-rose-400 border-rose-600",
    HOLD: "bg-slate-600/20 text-slate-300 border-slate-500",
  };
  return (
    <span className={`inline-flex items-center gap-1 rounded border px-2 py-0.5 text-xs font-semibold ${styles[action] || styles.HOLD}`}>
      {action}
      {confidence !== undefined && <span className="opacity-70">{Math.round(confidence * 100)}%</span>}
    </span>
  );
}
