import type { Metadata } from "next";
import "./globals.css";
import { NavBar } from "@/components/nav-bar";

export const metadata: Metadata = {
  title: "AShare AI Trader · A 股量化投研工作台",
  description: "实时行情、投研分析、模拟交易与策略回测（研究用途，不构成投资建议）",
};

const themeInit = `
(function () {
  try {
    var t = localStorage.getItem("ashare-theme");
    document.documentElement.classList.toggle("dark", t !== "light");
  } catch (e) {
    document.documentElement.classList.add("dark");
  }
})();
`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN" className="dark" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeInit }} />
      </head>
      <body className="min-h-screen bg-zinc-50 text-zinc-900 antialiased dark:bg-zinc-950 dark:text-zinc-100">
        <NavBar />
        <div className="mx-auto max-w-7xl px-4 pb-12">{children}</div>
        <footer className="border-t border-zinc-200 py-4 text-center text-xs text-zinc-400 dark:border-zinc-800 dark:text-zinc-600">
          数据仅供投研与模拟交易参考 · 本系统第一阶段禁止连接真实券商与自动下单
        </footer>
      </body>
    </html>
  );
}
