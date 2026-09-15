"use client";

/**
 * 标的详情弹窗的 **context 层**（2026-09-15 详情弹窗化）。
 *
 * ## 为什么要与组件实现分开
 * 消费者分两类，依赖方向必须区分：
 * - **业务侧**（StockLink / 市场页 / 助手…）只需要 `useSymbolDetail()`，不该顺带
 *   把 `StockDetailPanel` 那棵重子树（图表 + 多张表 + 自己的 WS）拉进依赖图；
 * - **弹窗实现侧**（`symbol-detail-modal.tsx`）才需要面板。
 *
 * 更关键的是**断环**：`components/detail/detail-modal.tsx` 里也有一个「查看该股
 * 详情」入口，它若反向 import `symbol-detail-modal`，就会形成
 * `detail-modal → symbol-detail-modal → stock-detail → detail/stock-events → detail-modal`
 * 的循环依赖。把 context 抽到本文件（**零业务依赖**，只 import react）后，
 * 该入口只依赖本文件，环即消失。
 */
import { createContext, useContext } from "react";

export interface SymbolDetailRequest {
  /** 标的代码：6 位个股（`600519`）或带市场前缀的指数（`sh000001`）。 */
  symbol: string;
  /** 图表区初始 tab（来自深链 `ct=`，见 lib/detail-tabs.ts）。 */
  chartTab?: "kline" | "minute" | "flow";
  /** 右列初始 tab（来自深链 `rt=`）。 */
  rightTab?:
    | "book"
    | "trades"
    | "trade"
    | "real"
    | "profile"
    | "info"
    | "speed"
    | "boards"
    | "dt";
}

export interface SymbolDetailCtx {
  open: (req: SymbolDetailRequest) => void;
  close: () => void;
}

export const SymbolDetailCtx = createContext<SymbolDetailCtx>({
  open: () => {},
  close: () => {},
});

/**
 * **弹窗宿主的内部状态通道**（仅 `symbol-detail-modal.tsx` 使用）。
 *
 * 为什么要单独一条 context：两个 Provider 存在**相互可达性**要求——
 * 1. 详情弹窗里渲染的面板会调 `useDetailModal()`（相关事件行）⇒ 弹窗必须渲染在
 *    `DetailModalProvider` **内层**；
 * 2. `detail-modal.tsx` 的「查看个股详情」入口要调 `useSymbolDetail()` ⇒ 它必须
 *    在 `SymbolDetailProvider` **内层**。
 *
 * 若让两个 Provider 直接嵌套，必然有一侧落在对方外层而拿到 noop 默认值
 * （症状：点了没反应，2026-09-09 已在通知抽屉踩过一次）。解法是把**状态**与
 * **渲染宿主**拆开：Provider 只管 state，渲染交由 `<SymbolDetailModalHost/>`，
 * 由 `app/layout.tsx` 把它放进 `DetailModalProvider` 内层——两个方向同时满足。
 */
export const SymbolDetailStateCtx = createContext<SymbolDetailRequest | null>(null);

export function useSymbolDetail(): SymbolDetailCtx {
  return useContext(SymbolDetailCtx);
}

/**
 * 「链接 → 弹窗触发器」的**统一点击判定**（2026-09-15）。
 *
 * 全站有大量形态各异的详情链接（`<Link>` / 裸 `<a>` / 带自定义样式的行内链接），
 * 它们不宜都换成 `StockLink`（样式与结构会被改写）。这个 helper 把**判定规则**
 * 收在一处，让各处只加一个 `onClick`：
 * - 左键、无修饰键 ⇒ 拦截默认导航，改为弹窗（用户不离开当前页）；
 * - `⌘/Ctrl/Shift/Alt + 点击`、中键、以及已被内部元素 `preventDefault` 的点击
 *   ⇒ **放行浏览器默认行为**（新标签/新窗口打开真实 URL）。
 *
 * 这样"点开详情"的行为一致，而链接语义（href、右键复制、分享）保持不变。
 */
export function symbolDetailClick(
  open: (req: SymbolDetailRequest) => void,
  req: SymbolDetailRequest,
): (e: React.MouseEvent) => void {
  return (e) => {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) {
      return;
    }
    e.preventDefault();
    open(req);
  };
}
