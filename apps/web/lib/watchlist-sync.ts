/**
 * 自选集合变化的跨组件通知（2026-09-01 简化：替代原 lib/events.ts CustomEvent 契约）。
 *
 * 评估结论（用户要求）：三种全局事件中只有这一条是真实跨组件树需求——
 * search-box / 详情面板（加自选）→ 工作台左栏（刷新），双方无共同祖先，
 * props 回调不可行。改用 15 行的模块级订阅：无 DOM CustomEvent、
 * 无事件名字符串、一个用途一个函数，类型直接由函数签名表达。
 *
 * 另外两种事件的去向：
 * - paperChanged → props 回调（trade-form/trade-panel 都在 StockDetailPanel 子树内）
 * - realChanged  → useRealPositions 的 reload 直调（跨实例由 15s 轮询兜底）
 */

type Listener = () => void;

const listeners = new Set<Listener>();

/** 订阅自选集合变化，返回取消订阅函数（适配 useEffect cleanup）。 */
export function subscribeWatchlist(fn: Listener): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

/** 自选集合发生变化（加/删/改组）后调用，通知所有订阅方立即刷新。 */
export function notifyWatchlistChanged(): void {
  listeners.forEach((fn) => fn());
}
