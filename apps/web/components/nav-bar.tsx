"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { SearchBox } from "@/components/search-box";

const LINKS = [
  { href: "/workbench", label: "工作台" },
  { href: "/market", label: "市场" },
  { href: "/watchlist", label: "自选" },
  { href: "/boards", label: "板块" },
  { href: "/heatmap", label: "云图" },
  { href: "/limit-up", label: "涨停池" },
  { href: "/themes", label: "题材梯队" },
  { href: "/screener", label: "选股器" },
  { href: "/longhu", label: "龙虎榜" },
  { href: "/backtest", label: "回测" },
  { href: "/alerts", label: "预警" },
];

export function NavBar() {
  const pathname = usePathname();

  // 主题只影响图标，交给 CSS（dark: 变体）切换：无需 state，也就不存在水合不一致
  function toggleTheme() {
    const next = !document.documentElement.classList.contains("dark");
    document.documentElement.classList.toggle("dark", next);
    try {
      localStorage.setItem("ashare-theme", next ? "dark" : "light");
    } catch {}
  }

  return (
    <header className="sticky top-0 z-40 border-b border-zinc-200 bg-white/90 backdrop-blur dark:border-zinc-800 dark:bg-zinc-950/90">
      <div className="mx-auto flex h-14 max-w-7xl items-center gap-6 px-4">
        <Link href="/workbench" className="whitespace-nowrap font-semibold tracking-tight">
          AShare <span className="text-up">AI</span> Trader
        </Link>
        <nav className="hidden items-center gap-1 md:flex">
          {LINKS.map((l) => {
            const active = pathname.startsWith(l.href);
            return (
              <Link
                key={l.href}
                href={l.href}
                className={`rounded-md px-3 py-1.5 text-sm transition-colors ${
                  active
                    ? "bg-zinc-100 font-medium text-zinc-900 dark:bg-zinc-800 dark:text-zinc-100"
                    : "text-zinc-500 hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-100"
                }`}
              >
                {l.label}
              </Link>
            );
          })}
        </nav>
        <div className="flex-1" />
        <SearchBox />
        <button
          onClick={toggleTheme}
          aria-label="切换主题"
          className="rounded-md border border-zinc-200 px-2 py-1.5 text-sm text-zinc-500 hover:text-zinc-900 dark:border-zinc-800 dark:text-zinc-400 dark:hover:text-zinc-100"
        >
          <>
            {/* 深色态（显示太阳=可切浅色） */}
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden
              className="hidden dark:block"
            >
              <circle cx="12" cy="12" r="4" />
              <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41" />
            </svg>
            {/* 浅色态（显示月亮=可切深色） */}
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden
              className="block dark:hidden"
            >
              <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
            </svg>
          </>
        </button>
      </div>
    </header>
  );
}
