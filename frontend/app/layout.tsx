import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "STX Swing Analyzer",
  description: "Finanz-News & Swing-Trading Signal-Analyse",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="de">
      <body className="min-h-screen">
        <nav className="sticky top-0 z-50 border-b border-slate-800 bg-slate-950/90 backdrop-blur">
          <div className="mx-auto flex max-w-6xl items-center gap-6 px-4 py-3">
            <Link href="/" className="text-lg font-bold text-sky-400">📈 STX</Link>
            <Link href="/" className="text-sm text-slate-300 hover:text-white">Dashboard</Link>
            <Link href="/top" className="text-sm text-slate-300 hover:text-white">Top-Signale</Link>
            <Link href="/watchlist" className="text-sm text-slate-300 hover:text-white">Watchlist</Link>
            <Link href="/portfolios" className="text-sm text-slate-300 hover:text-white">Portfolios</Link>
            <Link href="/sources" className="text-sm text-slate-300 hover:text-white">Datenquellen</Link>
            <span className="ml-auto text-xs text-slate-500">
              Keine Anlageberatung — automatisierte Analyse
            </span>
          </div>
        </nav>
        <main className="mx-auto max-w-6xl px-4 py-6">{children}</main>
      </body>
    </html>
  );
}
