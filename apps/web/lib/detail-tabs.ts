/**
 * 详情页右列 tab 的纯数据定义（2026-09-11 从 components/stock-detail.tsx 抽出）。
 *
 * 抽出的动机有两个：
 * 1. **可单测**——tab 列表原先内联在 900+ 行的组件里，只能靠渲染整个详情页来验证；
 * 2. **口径显式**——「指数能用哪些 tab」是一条规定，不是 UI 细节，应可被断言。
 *
 * 指数右列精简（2026-09-11 用户反馈「过多用不到的 tab」）：
 * - 盘口：免费源无指数撮合数据 → 面板恒显示「盘口数据不可用（免费源仅盘中提供）」
 * - 资讯：`/api/news|announcements|company/{指数代码}` 恒 502（接口只接受 6 位个股代码）
 * 保留「涨速 / 板块」——指数专属价值且接口实测可用。
 */

/** 详情页右列 tab 键（与 components/stock-detail 的 RightTab 同域）。 */
export type DetailRightTab =
  | "book"
  | "trades"
  | "trade"
  | "real"
  | "profile"
  | "info"
  | "speed"
  | "boards"
  | "dt";

/** 图表区 tab 键（与 components/stock-detail 的 ChartTab 同域）。 */
export type DetailChartTab = "kline" | "minute" | "flow";

/**
 * 深链参数解析（2026-09-11 从 app/workbench/page.tsx 抽出）。
 *
 * 抽出的动机：这是**跨模块契约**——URL 由 `lib/nav-targets.ts` 的构造器产出
 * （助手一键跳转 / 分享链接），由这里解析。两边一旦漂移，链接不会报错，
 * 而是**静默回落到默认 tab**（正是 P2-28② 要修的那类"点不到位"）。
 * 放在这里才能被 nav-targets.test.ts 交叉断言（同 INDEX_RIGHT_TABS 的理由）。
 *
 * 非法值一律 undefined（回落默认），不抛错——URL 是外部输入。
 */
export function parseChartTab(v: string | null): DetailChartTab | undefined {
  return v === "kline" || v === "minute" || v === "flow" ? v : undefined;
}

export function parseRightTab(v: string | null): DetailRightTab | undefined {
  return v === "book" ||
    v === "trades" ||
    v === "trade" ||
    v === "real" ||
    v === "profile" ||
    v === "info" ||
    v === "speed" ||
    v === "boards" ||
    v === "dt"
    ? v
    : undefined;
}

/**
 * 详情**深链 URL → 弹窗入参**解析（2026-09-15 详情弹窗化）。
 *
 * 背景：详情面板从「工作台专属右栏」升级为**全站可弹窗打开的公共组件**
 * （components/detail/symbol-detail-modal.tsx）。站内仍存在大量指向
 * `/workbench?symbol=…&ct=…&rt=…` 的既有链接（助手一键跳转、富文本实体链接、
 * 分享链接、/stock/ 中转页）——它们**不能失效**，但点击时不应再跳页，
 * 而应就地弹窗。
 *
 * 于是需要「URL 反解析」：把链接里的标的与 tab 意图还原成弹窗入参。它与
 * `lib/nav-targets.ts` 的构造器互为逆运算，故与 parseChartTab/parseRightTab
 * 同处一文件——构造 ↔ 解析 ↔ 消费三边漂移时，这里的交叉断言能立刻发现。
 *
 * 容错：非详情链接返回 null（调用方照常导航，不吞链接）；非法 tab 回落 undefined。
 */
export interface WorkbenchDetailTarget {
  symbol: string;
  chartTab?: DetailChartTab;
  rightTab?: DetailRightTab;
}

/** 合法标的形态：6 位代码，可带 sh/sz/bj 前缀（指数如 sh000001）。 */
const SYMBOL_RE = /^(?:[a-zA-Z]{2})?\d{6}$/;

export function parseWorkbenchDetailUrl(url: string | null | undefined): WorkbenchDetailTarget | null {
  if (!url) return null;
  const raw = url.trim();
  // 只解析**站内相对路径**：带协议的绝对 URL 与 protocol-relative（`//host/…`）
  // 一律拒绝——否则 `https://evil.com/workbench?symbol=600519` 会被误判成站内详情。
  if (/^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(raw) || raw.startsWith("//")) return null;
  let u: URL;
  try {
    // 占位 base 只为取 pathname/searchParams（不可路由，不参与判定）
    u = new URL(raw, "http://x.invalid");
  } catch {
    return null;
  }
  // /stock/{code} 语义别名（中转页形态）：等价于 /workbench?symbol={code}
  const m = /^\/stock\/([^/?#]+)$/.exec(u.pathname);
  if (m) {
    const decoded = decodeURIComponent(m[1]);
    return SYMBOL_RE.test(decoded) ? { symbol: decoded } : null;
  }
  if (u.pathname !== "/stock" && u.pathname !== "/workbench") return null;
  const symbol = (u.searchParams.get("symbol") ?? "").trim();
  if (!SYMBOL_RE.test(symbol)) return null;
  return {
    symbol,
    chartTab: parseChartTab(u.searchParams.get("ct")),
    rightTab: parseRightTab(u.searchParams.get("rt")),
  };
}

/** 指数右列可用 tab：精简后只剩这两个。 */
export const INDEX_RIGHT_TABS: ReadonlySet<DetailRightTab> = new Set<DetailRightTab>([
  "speed",
  "boards",
]);

/** 指数右列的默认 tab（首次打开指数时落到哪个）。 */
export const INDEX_DEFAULT_RIGHT_TAB: DetailRightTab = "speed";

/** 个股右列 tab 顺序（与历史一致）。 */
const STOCK_RIGHT_TABS: readonly (readonly [DetailRightTab, string])[] = [
  ["book", "盘口"],
  ["trades", "逐笔"],
  ["trade", "模拟交易"],
  // 真实持仓：券商实际成交的手工账本，与模拟交易完全独立
  ["real", "真实持仓"],
  ["profile", "资料"],
  ["dt", "做T"],
  ["info", "资讯"],
] as const;

/** 指数右列 tab 顺序。 */
const INDEX_TABS: readonly (readonly [DetailRightTab, string])[] = [
  ["speed", "涨速"],
  ["boards", "板块"],
] as const;

/** 按标的是否为指数返回右列 tab 列表（纯函数）。 */
export function rightTabsFor(isIndex: boolean): readonly (readonly [DetailRightTab, string])[] {
  return isIndex ? INDEX_TABS : STOCK_RIGHT_TABS;
}
