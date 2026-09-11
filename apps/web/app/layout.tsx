import type { Metadata } from "next";
import "./globals.css";
import { NavBar } from "@/components/nav-bar";
import { FloatingAssistant } from "@/components/assistant/floating-assistant";
import { DetailModalProvider } from "@/components/detail/detail-modal";

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
    <html lang="zh-CN" className="dark h-full" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeInit }} />
      </head>
      <body className="h-screen overflow-hidden flex flex-col bg-zinc-50 text-zinc-900 antialiased dark:bg-zinc-950 dark:text-zinc-100">
        {/* Provider 必须包住 NavBar（通知抽屉在里面用 useDetailModal），
            否则拿到默认 noop context——点无 url 通知没反应（2026-09-09 踩坑） */}
        <DetailModalProvider>
          <NavBar />
          {/* 内容区是唯一滚动域：单页布局锁定在可视区内，溢出交给容器内部滚动 */}
          <div className="flex-1 min-h-0">{children}</div>
          <FloatingAssistant />
        </DetailModalProvider>
      </body>
    </html>
  );
}
