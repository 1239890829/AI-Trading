/**
 * 统一路由跳转规范（docs/linkage-design.md §2）。
 *
 * 规则一：选中标的的唯一真相源是 URL（/workbench?symbol=…）。
 * - 页面内切换用 router.replace(workbenchUrl(s), { scroll: false })（不产生历史噪音）
 * - 跨页跳转用 Link / router.push(workbenchUrl(s))
 * - 列表页跳详情一律走 workbenchUrl()，不要手拼字符串——
 *   2026-08-31 的跨页面联动 bug（题材榜点个股，详情仍显示自选股）就是
 *   五处调用点各自拼 /stock/xxx、而中转页只读查询参数造成的。
 */

/** 详情页地址（唯一详情入口）。/stock/[symbol] 保留为语义别名，经其重定向到这里。 */
export function workbenchUrl(symbol: string): string {
  return `/workbench?symbol=${encodeURIComponent(symbol)}`;
}

/**
 * 跨页跳工作台并携带来源（2026-09-03 需求：所有与工作台联动的板块都提供返回入口，
 * 返回后原页面状态保留——来源页的 tab/选中态本来就活在 URL query 里，把整个
 * 「路径 + 查询串」原样装进 from 参数即可，无需每个页面自己序列化状态）。
 * SSR/纯函数环境退化为不带 from 的普通跳转。
 */
export function workbenchUrlWithBack(symbol: string): string {
  const base = workbenchUrl(symbol);
  if (typeof window === "undefined") return base;
  const from = window.location.pathname + window.location.search;
  if (!from.startsWith("/")) return base;
  return `${base}&from=${encodeURIComponent(from)}`;
}

/** from 路径 → 来源页中文名（返回按钮的文案；未知路径返回 null → 不渲染按钮）。 */
export function originLabel(from: string | null): string | null {
  if (!from || !from.startsWith("/")) return null;
  const path = from.split("?")[0];
  const labels: Record<string, string> = {
    "/intraday": "盘中跟踪",
    "/tape": "盘面",
    "/market": "市场",
    "/picks": "每日精选",
    "/research": "研究",
  };
  return labels[path] ?? null;
}

/** 工作台「上次查看标的」的 sessionStorage 键（无参数进入 /workbench 时的回退）。 */
export const LAST_SYMBOL_KEY = "ashare.workbench.lastSymbol";

/**
 * 解析 /stock/ 中转页的跳转目标。
 * 路径参数（/stock/600103）优先，查询参数（/stock?symbol=600103）兼容保留；
 * 容错剥掉市场前后缀（600105.SH、SH600105 → 600105）；无法解析返回 null。
 */
export function stockRedirectTarget(
  pathSymbol?: string | null,
  querySymbol?: string | null,
): string | null {
  const raw = (pathSymbol || querySymbol || "").trim();
  if (!raw) return null;
  const digits = raw.replace(/\D/g, "");
  if (!digits) return null;
  return workbenchUrl(digits.slice(0, 6));
}
