"use client";

import { useEffect, useState } from "react";

export default function ThemeToggle() {
  const [theme, setTheme] = useState<string>("dark");

  useEffect(() => {
    setTheme(document.documentElement.dataset.theme || "dark");
  }, []);

  function toggle() {
    const next = theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem("stx-theme", next);
    } catch {}
    setTheme(next);
  }

  return (
    <button
      onClick={toggle}
      title={theme === "dark" ? "Helles Theme" : "Dunkles Theme"}
      aria-label="Theme umschalten"
      className="rounded-full border border-slate-700 px-2.5 py-1 text-sm hover:border-sky-500"
    >
      {theme === "dark" ? "☀️" : "🌙"}
    </button>
  );
}
