import { ReactNode } from "react";
import { QualityBadge } from "@/components/quality-badge";
import { PanelBoundary } from "@/components/ui/panel-boundary";
import { sourceLabel, timeText } from "@/lib/format";
import type { Quality } from "@/types/market";


interface PanelProps {
  /** 标题；省略（且无右侧信息）时整行头部不渲染，把纵向空间全留给内容区。 */
  title?: ReactNode;
  children: ReactNode;
  source?: string;
  dataTimestamp?: string | null;
  quality?: Quality;
  qualityReasons?: string[];
  extra?: ReactNode;
  className?: string;
  bodyClassName?: string;
  /**
   * 透传给内建 `PanelBoundary` 的重置键（D-3，2026-09-12 评审批次 2）。
   *
   * **2026-09-13（§6.5b #4）起，错误态的清除有两条路（满足其一即可）**：
   * - **默认**：`title`（label）变化即清除——切 tab / 换面板这类"同一位置换视图"
   *   多数伴随标题变化，不再依赖调用点记得传；
   * - **显式**：`title` **不变**而内容换（工作台右列「自选股」各分组间 title 完全相同）
   *   label 判断不出来 ⇒ 按 `activeGroup` 传。
   *
   * **代价（已拍板接受）**：title 含计数的面板（数据刷新会改 title）每次数据变化
   * 清一次错误态、多重渲染一次失败的子树；若仍在失败，错误卡原样回归，
   * **不会掩盖持续故障**。
   *
   * 四种形态（默认覆盖 / 仍需传 / 不必传 / 数据刷新成本）都钉在
   * `panel-boundary.test.tsx` 的 `Panel.resetKey（D-3）`，改渲染结构翻转时那里会红。
   */
  resetKey?: unknown;
}

/**
 * 通用卡片面板（section + 头部 + body）。
 *
 * ⚠️ 全高面板契约（事件 Tab 滚动失效事故 ×2 的根因，2026-09-04 定案）：
 * 本组件 section 默认 height:auto —— 会随内容撑开、不受父容器约束。
 * 若内容区需要「父容器内滚动」而非「整页撑开」，必须让 section 高度有界：
 *   - 作为 flex 子项：className 加 `min-h-0 flex-1`（如云图 tab）；
 *   - 作为普通块级子元素（父级是 div.h-full 等）：className 必须加 `h-full`
 *     （如市场页事件 tab——漏掉它 ul 的 overflow-y-auto 永不触发，
 *     内容被页面 overflow-hidden 静默裁掉，且无任何报错）。
 * 自查口诀：Panel 是 tab 视图根节点 → 一定要有 h-full 或 flex-1+min-h-0。
 *
 * 错误边界（S2-6，2026-09-11）：**body 已内建 `PanelBoundary`**，单个面板渲染期
 * 抛错只会让这一块降级为错误卡，头部（标题/来源/质量徽标）与页面其余部分照常。
 * 此前只有路由级边界，任何面板抛错都会把整页换成错误卡——用户为看一个面板的
 * 失败丢掉全部已加载内容。
 *
 * 边界**只包 body 不包头部**：头部是纯展示（标题 + 徽标），几乎不可能抛错；
 * 把它留在边界外，失败时用户仍能看到"这是哪个面板坏了"，而不是一块匿名灰卡。
 * 注意 `title` 是 `ReactNode`，只有字符串形态才作为 label 传给边界。
 *
 * 边界抓不到**事件处理器与异步回调**里的异常（React 机制所限），那两类由
 * `useResource` 三态与根边界兜底，详见 `components/ui/panel-boundary.tsx`。
 */

export function Panel({ title, children, source, dataTimestamp, quality, qualityReasons, extra, className, bodyClassName, resetKey }: PanelProps) {
  // 头部占位约 41px；图表类面板的标题（股票名+图表类型）在页面别处已展示，
  // 保留纯属重复占位 —— 无标题且无右侧徽标时整行不渲染（2026-09-02）。
  const showHeader = Boolean(title || extra || quality || source || dataTimestamp);
  return (
    <section className={`flex min-w-0 flex-col overflow-hidden rounded-xl border border-zinc-200 dark:border-zinc-800 ${className ?? ""}`}>
      {showHeader && (
        <div className="flex items-center justify-between border-b border-zinc-200 bg-zinc-900/[0.03] px-4 py-2.5 dark:border-zinc-800 dark:bg-zinc-900/40">
          {/* min-w-0+truncate：长标题截断省略，不挤压右侧按钮/来源徽标（右侧 shrink-0 保完整） */}
          <h2 className="min-w-0 truncate text-sm font-medium text-zinc-900 dark:text-zinc-100">{title}</h2>
          <div className="flex shrink-0 items-center gap-3 text-xs text-zinc-600 dark:text-zinc-400">
            {extra}
            {/* 直接渲染，不加门控——可见性策略单点在 QualityBadge 内部
                （2026-09-14 口径统一；此前 `{quality && …}` 这类门控散在各调用点，
                正是「盘面显示正常 / 工作台不显示」漂移的成因）。 */}
            <QualityBadge quality={quality} reasons={qualityReasons} />
            {source && <span title="数据来源">{sourceLabel(source)}</span>}
            {dataTimestamp && <span title="数据时间">{timeText(dataTimestamp)}</span>}
          </div>
        </div>
      )}
      <div className={"flex-1 " + (bodyClassName ?? "overflow-auto")}>
        <PanelBoundary label={typeof title === "string" ? title : undefined} resetKey={resetKey}>{children}</PanelBoundary>
      </div>
    </section>
  );
}
