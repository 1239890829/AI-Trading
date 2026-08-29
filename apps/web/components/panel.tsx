import { ReactNode } from "react";
import { QualityBadge } from "@/components/quality-badge";
import { timeText } from "@/lib/format";
import type { Quality } from "@/types/market";

interface PanelProps {
  title: string;
  children: ReactNode;
  source?: string;
  dataTimestamp?: string | null;
  quality?: Quality;
  qualityReasons?: string[];
  extra?: ReactNode;
  className?: string;
  bodyClassName?: string;
}

export function Panel({ title, children, source, dataTimestamp, quality, qualityReasons, extra, className, bodyClassName }: PanelProps) {
  return (
    <section className={`flex min-w-0 flex-col overflow-hidden rounded-xl border border-zinc-200 dark:border-zinc-800 ${className ?? ""}`}>
      <div className="flex items-center justify-between border-b border-zinc-200 bg-zinc-900/[0.03] px-4 py-2.5 dark:border-zinc-800 dark:bg-zinc-900/40">
        <h2 className="text-sm font-medium text-zinc-200 dark:text-zinc-100">{title}</h2>
        <div className="flex items-center gap-3 text-xs text-zinc-400">
          {extra}
          {quality && <QualityBadge quality={quality} reasons={qualityReasons} />}
          {source && <span title="数据来源">{source}</span>}
          {dataTimestamp && <span title="数据时间">{timeText(dataTimestamp)}</span>}
        </div>
      </div>
      <div className={"flex-1 " + (bodyClassName ?? "overflow-auto")}>{children}</div>
    </section>
  );
}
