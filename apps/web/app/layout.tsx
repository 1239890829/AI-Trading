import type { Metadata } from "next";
import "./globals.css";
import "./controls.css";
import "./system.css";
import { NavBar } from "@/components/nav-bar";
import { FloatingAssistant } from "@/components/assistant/floating-assistant";
import { DetailModalProvider } from "@/components/detail/detail-modal";
import {
  SymbolDetailModalHost,
  SymbolDetailProvider,
} from "@/components/detail/symbol-detail-modal";

export const metadata: Metadata = {
  title: "AShare AI Trader · A 股量化投研工作台",
  description: "实时行情、投研分析、模拟交易与复盘研究（研究用途，不构成投资建议）",
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
      <body className="app-shell h-dvh overflow-hidden flex flex-col antialiased">
        {/* Provider 必须包住 NavBar（通知抽屉在里面用 useDetailModal），
            否则拿到默认 noop context——点无 url 通知没反应（2026-09-09 踩坑） */}
        <SymbolDetailProvider>
          <DetailModalProvider>
            <NavBar />
            {/* 内容区是唯一滚动域：单页布局锁定在可视区内，溢出交给容器内部滚动 */}
            <div id="workspace-content" tabIndex={-1} className="workspace-content flex-1 min-h-0">{children}</div>
            <FloatingAssistant />
            {/* 标的详情弹窗的渲染宿主，**必须留在 DetailModalProvider 内层**：
                弹窗里的详情面板会调 useDetailModal（相关事件行），反过来
                DetailModalBody 又要调 useSymbolDetail（「查看个股详情」入口）。
                两侧都必须在对方内层，故状态由外层 Provider 提供、渲染宿主放这里。
                详见 components/detail/symbol-detail-context.ts 的头注。 */}
            <SymbolDetailModalHost />
          </DetailModalProvider>
        </SymbolDetailProvider>
      </body>
    </html>
  );
}
