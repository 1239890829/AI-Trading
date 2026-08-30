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
