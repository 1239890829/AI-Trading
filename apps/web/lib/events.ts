/**
 * 全局 DOM 事件契约（架构联动方案 Phase 1，docs/architecture-linkage-plan-2026-09-01.md）。
 *
 * 此前三种事件名以裸字符串散落在 5 个文件里，且 watchlist-changed 派发后
 * 全站无监听方（2026-09-01 实锤断点 P1）。规则：
 * - 事件名只允许出现在本文件；派发/监听一律走 emitAppEvent / onAppEvent；
 * - payload 暂为空（各消费方自行重拉数据），未来需要载荷时在这里补类型。
 */

export const APP_EVENTS = {
  /** 自选集合变化（加/删/改组）→ 工作台左栏立即刷新 */
  watchlistChanged: "watchlist-changed",
  /** 模拟账户变动（下单/撤单/重置）→ 交易页签与持仓组刷新 */
  paperChanged: "paper-changed",
  /** 真实持仓账本变动（记一笔/改金额）→ 持仓分类与详情持仓页签刷新 */
  realChanged: "real-changed",
} as const;

export type AppEventName = (typeof APP_EVENTS)[keyof typeof APP_EVENTS];

export function emitAppEvent(name: AppEventName): void {
  window.dispatchEvent(new CustomEvent(name));
}

/** 返回取消监听函数（适配 useEffect cleanup）。 */
export function onAppEvent(name: AppEventName, handler: () => void): () => void {
  window.addEventListener(name, handler);
  return () => window.removeEventListener(name, handler);
}
