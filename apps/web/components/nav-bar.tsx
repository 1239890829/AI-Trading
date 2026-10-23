"use client";

import { AccessButton, useDeviceAccess } from "@/components/access/device-access";
import { IconButton } from "@/components/ui/icon-button";
import { HugeiconsIcon } from "@hugeicons/react";
import Sun03Icon from "@hugeicons/core-free-icons/Sun03Icon";
import Moon02Icon from "@hugeicons/core-free-icons/Moon02Icon";
import DashboardSquare01Icon from "@hugeicons/core-free-icons/DashboardSquare01Icon";
import Target02Icon from "@hugeicons/core-free-icons/Target02Icon";
import Analytics01Icon from "@hugeicons/core-free-icons/Analytics01Icon";
import BookOpen01Icon from "@hugeicons/core-free-icons/BookOpen01Icon";
import { ControlHint } from "@/components/ui/control-hint";
import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { Suspense, useLayoutEffect, useRef } from "react";
import { CommandPalette } from "@/components/ui/command-palette";
import { TaskBrowser } from "@/components/ui/task-browser";
import { activeTask, TASK_LINKS } from "@/lib/task-navigation";
import { SearchBox } from "@/components/search-box";
import { NotificationBell } from "@/components/notifications/notification-drawer";

const TASK_ICONS = {
  workspace: DashboardSquare01Icon,
  opportunity: Target02Icon,
  market: Analytics01Icon,
  research: BookOpen01Icon,
};

function NavBarInner() {
  const {device} = useDeviceAccess();
  const pathname = usePathname();
  const params = useSearchParams();
  const activeId = activeTask(pathname, new URLSearchParams(params.toString()));

  const nav = useRef<HTMLElement>(null);
  const marker = useRef<HTMLSpanElement>(null);
  useLayoutEffect(() => {
    function measure() {
      const selected = nav.current?.querySelector<HTMLElement>('[aria-current="page"]');
      if (!marker.current) return;
      marker.current.style.opacity = selected ? "1" : "0";
      if (selected) {
        // Keep the rail inside its link. Scaling a 1px rounded shape stretches
        // its end caps, and integer offsetWidth can overlap the next gap.
        const bounds = selected.getBoundingClientRect();
        const origin = nav.current!.getBoundingClientRect();
        marker.current.style.width = `${Math.max(0, bounds.width - 12)}px`;
        marker.current.style.transform = `translateX(${bounds.left - origin.left + nav.current!.scrollLeft + 6}px)`;
      }
    }
    measure();
    const observer = typeof ResizeObserver === "function" ? new ResizeObserver(measure) : null;
    if(nav.current) observer?.observe(nav.current);
    nav.current?.querySelectorAll("a").forEach(link => observer?.observe(link));
    window.addEventListener("resize",measure);
    return () => {observer?.disconnect();window.removeEventListener("resize",measure);};
  },[activeId]);

  // 主题只影响图标，交给 CSS（dark: 变体）切换：无需 state，也就不存在水合不一致
  function toggleTheme() {
    const next = !document.documentElement.classList.contains("dark");
    document.documentElement.classList.toggle("dark", next);
    try {
      localStorage.setItem("ashare-theme", next ? "dark" : "light");
    } catch {}
  }

  return (
    <header className="workspace-header sticky top-0 z-40">
      <a className="skip-link" href="#workspace-content">跳到工作区</a>
      <div className="task-header mx-auto flex max-w-[1600px] flex-wrap items-center">
        <Link href="/workbench?mode=watch" aria-label="AShare AI Trader" className="brand-wordmark shrink-0 whitespace-nowrap font-semibold">
          <span className="brand-mark" aria-hidden="true"><i/><i/><i/></span><span>AShare<span className="brand-descriptor"> 研判</span></span>
        </Link>
        <nav ref={nav} aria-label="主要任务" className="task-navigation order-last flex w-full items-center gap-1 overflow-x-auto lg:order-none lg:w-auto">
          <span ref={marker} className="navigation-marker" aria-hidden="true" />
          {TASK_LINKS.map((l) => {
            const active = activeId === l.id;
            return (
              <Link
                key={l.href}
                href={l.href}
                aria-current={active ? "page" : undefined}
                className={`shrink-0 whitespace-nowrap rounded-md px-3 py-2 text-sm transition-colors ${
                  active
                    ? "bg-zinc-100 font-medium text-zinc-900 dark:bg-zinc-800 dark:text-zinc-100"
                    : "text-zinc-600 hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-100"
                }`}
              >
                <HugeiconsIcon icon={TASK_ICONS[l.id]} size={16} strokeWidth={1.6} aria-hidden="true" />
                <span>{l.label}</span>
              </Link>
            );
          })}
        </nav>
        <div className="header-spacer flex-1" />
        <SearchBox collapsible />
        <div className="header-tools" aria-label="全局工具">
        <CommandPalette maintenance={Boolean(device?.maintenance)} />
        <NotificationBell />
        <TaskBrowser maintenance={Boolean(device?.maintenance)} />
        <AccessButton />
        <ControlHint content="切换浅色或深色主题"><IconButton
          onClick={toggleTheme}
          aria-label="切换主题"
          className="theme-switch rounded-md border border-zinc-200 px-2 py-1.5 text-sm text-zinc-600 hover:text-zinc-900 dark:border-zinc-800 dark:text-zinc-400 dark:hover:text-zinc-100"
        >
          <HugeiconsIcon icon={Sun03Icon} size={18} strokeWidth={1.6} className="hidden dark:block" aria-hidden="true" />
          <HugeiconsIcon icon={Moon02Icon} size={18} strokeWidth={1.6} className="block dark:hidden" aria-hidden="true" />
        </IconButton></ControlHint>
        </div>
      </div>
    </header>
  );
}

export function NavBar() {
  return <Suspense fallback={<header className="h-14 border-b border-zinc-200 dark:border-zinc-800" />}><NavBarInner /></Suspense>;
}
