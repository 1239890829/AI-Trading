"use client";

/**
 * 个股 / 指数详情弹窗（2026-09-15 用户需求）。
 *
 * ## 背景
 * 详情面板（`components/stock-detail.tsx` 的 `StockDetailPanel`）此前只长在
 * 工作台右栏。盘面 / 市场 / 题材 / 精选 / 猎场 / 助手等模块要看某只个股或某个
 * 指数的详情，只能**跳转 `/workbench?symbol=…`**——用户离开当前页面、丢掉
 * 手上的列表上下文，看完还得按「← 返回来源页」回来。
 *
 * ## 做法：复用而非重建
 * 弹窗内**原样渲染同一个 `StockDetailPanel`**，不复制一份详情实现：
 * - 面板是**自治**的（给 `symbol` 就自行取 K 线/盘口/逐笔/分时/资金/财务/资讯）；
 * - 面板**不读 URL、不依赖全局 context**，`?ct=` / `?rt=` 由调用方转为 props；
 * - 面板**自带 WS**（上游不传 `liveQuote` 时）——这正是它被 `/stock/[symbol]`
 *   复用的既有契约，弹窗复用与设计意图一致，不会出现"只有一半数据"的弹窗。
 * ⇒ 任何面板能力的增强（新 tab、新指标）自动惠及弹窗，不存在两份真相源。
 *
 * ## 尺寸自适应
 * 弹窗宽高随视口伸缩、四周留边：`min(1600px, 96vw) × min(1000px, 92vh)`。
 * 面板本身是「容器给高度、内部自管滚动」的设计（见 `components/panel.tsx`
 * 的全高契约），故内层用 **grid 包裹**使面板高度 = 弹窗内容区高度——
 * 与工作台 `grid-cols-[340px,minmax(0,1fr)]` 的父网格同构。若改用普通 block
 * 容器，面板会退化为内容高度，图表无法撑满。
 *
 * ## 与 `detail-modal.tsx` 的分工
 * - `DetailModalProvider`：**内容**详情（资讯/事件/题材/资金），payload 是标题+正文；
 * - `SymbolDetailProvider`（本文件）：**标的**详情（个股/指数全功能面板）。
 * 两者职责不同，各自单例；弹窗内触发的资讯/事件详情走前者，层级由 z-index
 * 与挂载顺序决定（见下）。
 *
 * ## 层级
 * 本弹窗 `z-50`（与 `news-modal` / `concept-detail-modal` / `pick-detail-modal`
 * 同级）——**刻意不高于 `detail-modal` 的 `z-[60]`**：面板内点事件时，事件详情
 * 弹窗必须盖在标的详情之上。同级弹窗之间靠"后挂载者在上"的自然顺序排序，
 * 故从概念弹窗里再开标的详情也能正确覆盖。
 */
import { useCallback, useContext, useMemo, useState } from "react";
import { usePathname, useRouter } from "next/navigation";

import { StockDetailPanel } from "@/components/stock-detail";
import { PanelBoundary } from "@/components/ui/panel-boundary";
import { ModalShell } from "@/components/ui/modal-shell";
import {
  SymbolDetailCtx,
  SymbolDetailStateCtx,
  useSymbolDetail,
  type SymbolDetailRequest,
} from "@/components/detail/symbol-detail-context";
import { isIndexSymbol } from "@/lib/api/client";
import { workbenchTabUrl } from "@/lib/routing";

// context / 点击判定在零依赖的 symbol-detail-context.ts（断环，见该文件头注）；
// 这里 re-export，调用方只认一个 import 源。
export { symbolDetailClick, useSymbolDetail } from "@/components/detail/symbol-detail-context";
export type { SymbolDetailRequest } from "@/components/detail/symbol-detail-context";

/**
 * 提供 `useSymbolDetail()` 能力。**只做 state，不渲染弹窗**——弹窗由
 * `<SymbolDetailModalHost/>` 渲染，而后者必须挂在 `DetailModalProvider` 内层。
 * 为什么必须拆开：见 `symbol-detail-context.ts` 的 `SymbolDetailStateCtx` 头注。
 */
export function SymbolDetailProvider({ children }: { children: React.ReactNode }) {
  const [req, setReq] = useState<SymbolDetailRequest | null>(null);
  const pathname = usePathname();
  const router = useRouter();

  const open = useCallback(
    (r: SymbolDetailRequest) => {
      // 已在工作台：右栏就是同一份详情面板，再叠一层弹窗纯属冗余，且会让
      // 「左栏点自选 = 切右栏」与「搜索框选股 = 弹窗」两条路径行为分叉。
      // 统一按**页内切换**处理（router.replace 不产生历史噪音，见 lib/routing.ts
      // 规则一）。`from` 原样保留，否则跳过来再切股会丢掉「← 返回来源页」。
      if (pathname === "/workbench") {
        const from =
          typeof window === "undefined" ? null : new URLSearchParams(window.location.search).get("from");
        router.replace(
          workbenchTabUrl(r.symbol, { chartTab: r.chartTab, rightTab: r.rightTab, from }),
          { scroll: false },
        );
        return;
      }
      setReq(r);
    },
    [pathname, router],
  );

  const close = useCallback(() => setReq(null), []);
  const value = useMemo(() => ({ open, close }), [open, close]);

  return (
    <SymbolDetailCtx.Provider value={value}>
      <SymbolDetailStateCtx.Provider value={req}>{children}</SymbolDetailStateCtx.Provider>
    </SymbolDetailCtx.Provider>
  );
}

/**
 * 弹窗渲染宿主。**必须挂在 `app/layout.tsx` 的 `DetailModalProvider` 内层**，
 * 否则弹窗里的详情面板拿不到 `useDetailModal`（相关事件行点不开）。
 */
export function SymbolDetailModalHost() {
  const req = useContext(SymbolDetailStateCtx);
  const { close } = useSymbolDetail();
  return req ? <SymbolDetailModalBody req={req} onClose={close} /> : null;
}

function SymbolDetailModalBody({ req, onClose }: { req: SymbolDetailRequest; onClose: () => void }) {
  const isIndex = isIndexSymbol(req.symbol);
  const kindLabel = isIndex ? "指数详情" : "个股详情";

  return (
    <ModalShell
      onClose={onClose}
      label={`${kindLabel} ${req.symbol}`}
      testid="symbol-detail-modal"
      size="lg"
      header={
        <div className="flex items-center gap-2">
          <span className="text-xs font-medium text-zinc-800 dark:text-zinc-100">{kindLabel}</span>
          <span className="font-mono text-[11px] text-zinc-500 dark:text-zinc-400">{req.symbol}</span>
        </div>
      }
      bodyClassName="overflow-hidden p-2"
    >
      {/* grid + min-h-0 flex-1：让面板高度 = 内容区高度（全高契约，见文件头注）。
          PanelBoundary 健康时不产生额外 DOM 节点 ⇒ 网格项仍是面板自身。 */}
      <div className="grid min-h-0 flex-1">
        <PanelBoundary key={req.symbol} label={kindLabel}>
          <StockDetailPanel symbol={req.symbol} chartTab={req.chartTab} rightTab={req.rightTab} />
        </PanelBoundary>
      </div>
    </ModalShell>
  );
}
