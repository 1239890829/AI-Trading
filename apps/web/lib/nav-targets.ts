/**
 * 助手回答「一键跳转」的入口注册表（唯一真相源）。
 *
 * 背景：此前助手只有两个落点（workbenchUrl / themesUrl），市场/研究/精选/盘中
 * 的具体 tab 与条目都跳不了。2026-09-06 按 docs/assistant-optimization-plan.md §1.2
 * 收敛为一张注册表 + 三类定位方式：
 *   A URL 深链（首选，状态已在 query，可书签、可分享）
 *   B 初值深链（目标页 state 从 query 初始化，见 workbench rt/ct、research date）
 *   C 锚点滚动（长页分区，见 intraday ?sec=）
 *
 * 纪律：
 * - 所有跳转必须经 `buildNav` 产出，业务侧绝不裸拼 URL（与 workbenchUrl 同理）；
 * - 产出必经 `isAllowedNav` 守卫：只允许站内白名单路径，防模型/文本注入站外链接；
 * - 别名只收**足够具体**的词（"涨停池"而非"涨停"），否则正文满屏链接反而干扰阅读。
 */
import { workbenchUrl, themesUrl, tapeUrl } from "@/lib/routing";

/** 允许跳转的站内路径白名单（不含 /stock 中转页：它只是重定向，不是落点）。 */
export const NAV_ALLOWED_PATHS = [
  "/workbench",
  "/tape",
  "/market",
  "/hunting",
  "/research",
] as const;

/** 入口构造器。参数缺失时落到该板块的默认视图，不报错。 */
export const NAV_TARGETS = {
  stock: (symbol: string) => workbenchUrl(symbol),
  // 图表区 tab（ct 与 StockDetailPanel 的 ChartTab 同域：kline|minute|flow）
  stock_kline: (symbol: string) => `${workbenchUrl(symbol)}&ct=kline`,
  stock_minute: (symbol: string) => `${workbenchUrl(symbol)}&ct=minute`,
  stock_flow: (symbol: string) => `${workbenchUrl(symbol)}&ct=flow`,
  // 右栏 tab（rt 与 RightTab 同域：book|trades|trade|real|profile|info|speed|boards）
  stock_trades: (symbol: string) => `${workbenchUrl(symbol)}&rt=trades`,
  stock_profile: (symbol: string) => `${workbenchUrl(symbol)}&rt=profile`,
  stock_info: (symbol: string) => `${workbenchUrl(symbol)}&rt=info`,
  theme_ladder: (focus?: string) => themesUrl(focus),
  limitup: (date?: string) => tapeUrl("limitup", date ? { date } : undefined),
  limitdown: (date?: string) => tapeUrl("limitdown", date ? { date } : undefined),
  longhu: () => tapeUrl("longhu"),
  market_overview: () => "/market?tab=overview",
  market_fund: () => "/market?tab=fund",
  market_heatmap: () => "/market?tab=heatmap",
  market_events: () => "/market?tab=events",
  // 2026-09-08 猎场融合：/picks /intraday → /hunting（tag: pick=精选 watch=跟踪）
  picks: () => "/hunting?tag=pick",
  picks_review: () => "/hunting?review=1&tag=pick",
  hunting: () => "/hunting",
  research_backtest: () => "/research?tab=backtest",
  research_alerts: () => "/research?tab=alerts",
  research_review: (date?: string) => `/research?tab=review${date ? `&date=${encodeURIComponent(date)}` : ""}`,
  intraday: () => "/hunting?tag=watch",
  intraday_brief: () => "/hunting?sec=brief",
  intraday_opportunity: () => "/hunting?sec=opportunity",
  intraday_reminders: () => "/hunting?sec=reminders",
  intraday_theme: (theme: string) => `/hunting?theme=${encodeURIComponent(theme)}`,
} as const;

export type NavKey = keyof typeof NAV_TARGETS;

/**
 * 功能别名表（前端词典匹配用，无参）。
 * 只收录"说了就该能点进去"的具体功能名；泛词（如"复盘"）不收——
 * 它在正文里高频出现，全部渲染成链接会盖过内容本身。
 */
export const NAV_ALIASES: Record<string, NavKey> = {
  涨停池: "limitup",
  涨停生态: "limitup",
  涨停梯队: "limitup",
  跌停池: "limitdown",
  跌停板: "limitdown",
  龙虎榜: "longhu",
  题材梯队: "theme_ladder",
  题材榜: "theme_ladder",
  市场资金: "market_fund",
  资金流向: "market_fund",
  主力资金: "market_fund",
  热力云图: "market_heatmap",
  热力图: "market_heatmap",
  云图: "market_heatmap",
  事件面板: "market_events",
  事件流: "market_events",
  市场概览: "market_overview",
  每日精选: "picks",
  选股复盘: "picks_review",
  精选复盘: "picks_review",
  猎场: "hunting",
  复盘报告: "research_review",
  方法论复盘: "research_review",
  策略回测: "research_backtest",
  预警规则: "research_alerts",
  盘前简报: "intraday_brief",
  盘中机会: "intraday_opportunity",
  盘中提醒: "intraday_reminders",
  盘中跟踪: "intraday",
};

/** 需要参数的入口（个股类由实体匹配提供参数，不参与别名无参匹配）。 */
const PARAM_KEYS: ReadonlySet<NavKey> = new Set<NavKey>([
  "stock",
  "stock_kline",
  "stock_minute",
  "stock_flow",
  "stock_trades",
  "stock_profile",
  "stock_info",
  "intraday_theme",
]);

/** 别名按长度降序（最长匹配优先："涨停池" 先于 "涨停"）。 */
export function navAliasWords(): string[] {
  return Object.keys(NAV_ALIASES).sort((a, b) => b.length - a.length);
}

/**
 * 安全守卫：只放行站内白名单路径的相对 URL。
 * 拦两类注入：① 协议相对/绝对外链（//evil.com、https://…）；② 白名单外路径。
 * 模型输出或正文里的链接都必须先过这一关，未过即当纯文本渲染。
 */
export function isAllowedNav(url: string): boolean {
  if (!url.startsWith("/") || url.startsWith("//")) return false;
  const path = url.split(/[?#]/)[0];
  return (NAV_ALLOWED_PATHS as readonly string[]).includes(path);
}

/** 按 key 构造跳转 URL；守卫未过返回 null（调用方应降级为纯文本，绝不渲染成链接）。 */
export function buildNav(key: NavKey, ...args: unknown[]): string | null {
  const builder = NAV_TARGETS[key] as ((...a: unknown[]) => string) | undefined;
  if (!builder) return null;
  let url: string;
  try {
    url = builder(...args);
  } catch {
    return null;
  }
  return isAllowedNav(url) ? url : null;
}

/** 别名 → URL（无参入口）。未命中或守卫未过返回 null。 */
export function navUrlForAlias(alias: string): string | null {
  const key = NAV_ALIASES[alias];
  if (!key || PARAM_KEYS.has(key)) return null;
  return buildNav(key);
}

export const NAV_LABELS: Record<NavKey, string> = {
  stock: "个股详情",
  stock_kline: "K 线图",
  stock_minute: "分时图",
  stock_flow: "资金流向图",
  stock_trades: "逐笔成交",
  stock_profile: "财务资料",
  stock_info: "公告新闻",
  theme_ladder: "题材梯队",
  limitup: "涨停池",
  limitdown: "跌停池",
  longhu: "龙虎榜",
  market_overview: "市场概览",
  market_fund: "市场资金",
  market_heatmap: "热力云图",
  market_events: "事件面板",
  picks: "每日精选",
  picks_review: "选股复盘",
  hunting: "猎场",
  research_backtest: "策略回测",
  research_alerts: "预警规则",
  research_review: "复盘报告",
  intraday: "盘中跟踪",
  intraday_brief: "盘前简报",
  intraday_opportunity: "盘中机会",
  intraday_reminders: "盘中提醒",
  intraday_theme: "盘中题材",
};
