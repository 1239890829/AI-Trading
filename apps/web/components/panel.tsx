import { ReactNode } from "react";
import { QualityBadge } from "@/components/quality-badge";
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
 */

export function Panel({ title, children, source, dataTimestamp, quality, qualityReasons, extra, className, bodyClassName }: PanelProps) {
  // 头部占位约 41px；图表类面板的标题（股票名+图表类型）在页面别处已展示，
  // 保留纯属重复占位 —— 无标题且无右侧徽标时整行不渲染（2026-09-02）。
  const showHeader = Boolean(title || extra || quality || source || dataTimestamp);
  return (
    <section className={`flex min-w-0 flex-col overflow-hidden rounded-xl border border-zinc-200 dark:border-zinc-800 ${className ?? ""}`}>
      {showHeader && (
        <div className="flex items-center justify-between border-b border-zinc-200 bg-zinc-900/[0.03] px-4 py-2.5 dark:border-zinc-800 dark:bg-zinc-900/40">
          {/* min-w-0+truncate：长标题截断省略，不挤压右侧按钮/来源徽标（右侧 shrink-0 保完整） */}
          <h2 className="min-w-0 truncate text-sm font-medium text-zinc-200 dark:text-zinc-100">{title}</h2>
          <div className="flex shrink-0 items-center gap-3 text-xs text-zinc-400">
            {extra}
            {quality && <QualityBadge quality={quality} reasons={qualityReasons} />}
            {source && <span title="数据来源">{sourceLabel(source)}</span>}
            {dataTimestamp && <span title="数据时间">{timeText(dataTimestamp)}</span>}
          </div>
        </div>
      )}
      <div className={"flex-1 " + (bodyClassName ?? "overflow-auto")}>{children}</div>
    </section>
  );
}
