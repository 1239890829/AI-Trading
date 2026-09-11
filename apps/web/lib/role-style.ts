/**
 * 梯队角色配色（S2-10，2026-09-11）—— **全站唯一一份**。
 *
 * ## 权威集来自后端
 * 键集 = `app/picks/echelon.py` 的 `ROLE_BASE_SCORE`（11 键）。它是角色打分表，
 * 覆盖后端全部产出方：
 * - `theme_service.classify_role()`         → 反包 / 空间板 / 龙头 / 中军 / 补涨 / 跟风 / 首板
 * - `theme_service` 的 `broken_ladder` 段    → 断板
 * - `echelon.classify_non_limit_up_role()`  → 中军 / 领涨 / 滞涨 / 同步
 *
 * ## 为什么合并（此前的两份表各有一个真缺陷）
 * 1. **题材看板的表缺 `领涨/滞涨/同步`** ⇒ `ROLE_STYLE[role]` 取到 `undefined`，
 *    徽标**静默无色**。`Record<string, string>` 索引签名让 tsc 放行，编译期抓不到；
 *    深色底上一个无样式徽标近乎不可见，是"看着像没加载完"的那类缺陷。
 * 2. **同一角色两页颜色不同**：题材看板 `中军=amber / 反包=violet`，猎场
 *    `中军=sky / 反包=amber` —— 同一顶帽子两种颜色，读起来像两个不同概念。
 *
 * ## 色阶约定
 * 按**梯队地位由高到低**由暖到冷：定高度的最热（空间板 rose → 龙头 orange），
 * 中军/反包/领涨/补涨各占一个可辨色相，跟风及以下退到中性灰。
 * 断板额外加删除线（语义装饰，不只是配色）。
 *
 * ## 跨端守卫
 * 键集必须与后端 `ROLE_BASE_SCORE` **全等**，由
 * `backend/tests/test_role_style.py` 逐键比对（仅后端有 / 仅前端有 分别报出）。
 * 后端新增角色而此处未跟进 → 该测试变红，不会等到用户在界面上发现无色徽标。
 */

/**
 * 未知角色的兜底样式。
 *
 * 存在的理由：`ROLE_STYLE` 是索引签名类型，`ROLE_STYLE[role]` 永远是 `string`
 * 而非 `string | undefined`，**tsc 不会拦住漏配**。历史上正是靠这一点漏掉了
 * 三个键且无人发现。渲染侧一律走 `roleClass()`，未知角色退化为兜底样式而不是
 * `undefined`（无样式徽标在深色底上近乎不可见）。
 *
 * **刻意用虚线与透明底**，与任何真实角色的实线填充区分开：真实角色里
 * `同步/滞涨/跟风` 也是中性灰，若兜底与它们同款，"漏配"就会伪装成"低档角色"
 * 而永远查不出来。未知就该**看着像未知**（三态纪律：未知不伪装成已知）。
 */
export const ROLE_FALLBACK_STYLE =
  "border-dashed border-zinc-400 bg-transparent text-zinc-500 dark:border-zinc-600 dark:text-zinc-400";

/** 梯队角色 → 徽标样式类。键集 = 后端 `echelon.ROLE_BASE_SCORE`（11 键，全等）。 */
export const ROLE_STYLE: Record<string, string> = {
  空间板: "border-rose-500/50 bg-rose-500/15 text-rose-700 dark:text-rose-300",
  龙头: "border-orange-500/50 bg-orange-500/15 text-orange-800 dark:text-orange-300",
  中军: "border-amber-500/50 bg-amber-500/15 text-amber-800 dark:text-amber-300",
  反包: "border-violet-500/50 bg-violet-500/15 text-violet-700 dark:text-violet-300",
  领涨: "border-teal-500/50 bg-teal-500/15 text-teal-700 dark:text-teal-300",
  补涨: "border-sky-500/50 bg-sky-500/15 text-sky-700 dark:text-sky-300",
  首板: "border-zinc-200 bg-zinc-50 text-zinc-600 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-400",
  同步: "border-zinc-300 bg-zinc-100 text-zinc-600 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-400",
  跟风: "border-zinc-300 bg-zinc-100 text-zinc-600 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
  滞涨: "border-zinc-300 bg-zinc-100 text-zinc-600 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-400",
  断板: "border-zinc-300 bg-zinc-100 text-zinc-600 line-through dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-400",
};

/**
 * 取角色样式。**渲染侧一律用这个，不要直接索引 `ROLE_STYLE`**——
 * 直接索引会在角色缺配/后端新增角色时静默给出 `undefined`。
 */
export function roleClass(role: string | null | undefined): string {
  if (!role) return ROLE_FALLBACK_STYLE;
  return ROLE_STYLE[role] ?? ROLE_FALLBACK_STYLE;
}
