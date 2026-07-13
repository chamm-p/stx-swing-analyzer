import type { Metadata } from "next";
import Link from "next/link";
import ThemeToggle from "@/components/ThemeToggle";
import UserMenu from "@/components/UserMenu";
import "./globals.css";

export const metadata: Metadata = {
  title: "STX Swing Analyzer",
  description: "Finanz-News & Swing-Trading Signal-Analyse",
};

// Theme vor dem ersten Paint setzen (kein Hell/Dunkel-Blitz)
const themeInit = `(function(){try{document.documentElement.dataset.theme=localStorage.getItem("stx-theme")||"dark";}catch(e){}})();`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="de" suppressHydrationWarning>
      <body className="min-h-screen">
        <script dangerouslySetInnerHTML={{ __html: themeInit }} />
        <nav className="sticky top-0 z-50 border-b border-slate-800 bg-slate-950/90 backdrop-blur">
          <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-5 gap-y-2 px-4 py-3">
            <Link href="/" className="text-lg font-bold text-sky-400">📈 STX</Link>
            <Link href="/" className="text-sm text-slate-300 hover:text-white">Dashboard</Link>
            <Link href="/top" className="text-sm text-slate-300 hover:text-white">Top-Signale</Link>
            <Link href="/watchlist" className="text-sm text-slate-300 hover:text-white">Watchlist</Link>
            <Link href="/portfolios" className="text-sm text-slate-300 hover:text-white">Portfolios</Link>
            <Link href="/review" className="text-sm text-slate-300 hover:text-white">Review</Link>
            <Link href="/backtest" className="text-sm text-slate-300 hover:text-white">Backtest</Link>
            <Link href="/settings" className="text-sm text-slate-300 hover:text-white">⚙️ Einstellungen</Link>
            <span className="ml-auto hidden text-xs text-slate-500 lg:block">
              Keine Anlageberatung
            </span>
            <UserMenu />
            <ThemeToggle />
          </div>
        </nav>
        <main className="mx-auto max-w-6xl px-4 py-6">{children}</main>
      </body>
    </html>
  );
}
