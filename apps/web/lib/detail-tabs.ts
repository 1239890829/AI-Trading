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
