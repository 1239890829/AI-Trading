/** 资料页签：板块标签分组 + 主营业务 + 公司简介 + 最近财报。纯展示。
 *  fins 三态（审查 F2/R2）：undefined=加载中 → 骨架；null=确认无 → 文案；
 *  数组=渲染。此前加载中与确认空同显「暂无财报数据」抢跑。 */
import { fmt, pctColor, pctText } from "@/lib/format";
import { Skeleton } from "@/components/ui/loading";

/** 公司资料（/api/company 返回的超集；类型随消费方窄化）。 */
export interface CompanyProfile {
  name?: string | null;
  industry?: string | null;
  profile?: string | null;
  main_business?: string | null;
  csrc_industry?: string | null;
  region?: string | null;
  boards?: string[];
  board_groups?: { industry: string[]; region: string[]; concept: string[]; style_index: string[] };
  core_themes?: string[];
  source: string;
}

export interface FinRow {
  report_date: string;
  revenue?: number | null;
  revenue_yoy?: number | null;
  net_profit?: number | null;
  profit_yoy?: number | null;
  gross_margin?: number | null;
  roe?: number | null;
  eps?: number | null;
  source: string;
}

export type BoardRows = [string, string[], string][];

export function ProfilePanel({
  boardRows,
  company,
  fins,
}: {
  boardRows: BoardRows;
  company: CompanyProfile | null;
  fins: FinRow[] | null | undefined;
}) {
  return (
    <div className="px-3 py-2 text-xs">
      {boardRows
        .filter(([, items]) => items.length > 0)
        .map(([label, items, tone]) => (
          <div key={label} className="mb-2.5">
            <div className="mb-1 text-zinc-600 dark:text-zinc-400">{label}</div>
            <div className="flex flex-wrap gap-1">
              {items.map((b) => (
                <span key={b} className={`rounded bg-zinc-100 px-1.5 py-0.5 dark:bg-zinc-800 ${tone}`}>
                  {b}
                </span>
              ))}
            </div>
          </div>
        ))}
      {company?.main_business && (
        <div className="mb-2">
          <div className="mb-1 text-zinc-600 dark:text-zinc-400">主营业务</div>
          <div className="leading-relaxed text-zinc-800 dark:text-zinc-200">{company.main_business}</div>
        </div>
      )}
      {company?.profile && (
        <div className="mb-3">
          <div className="mb-1 text-zinc-600 dark:text-zinc-400">公司简介</div>
          <div className="line-clamp-5 leading-relaxed text-zinc-600 dark:text-zinc-400" title={company.profile}>
            {company.profile}
          </div>
        </div>
      )}
      <div className="mb-1 text-zinc-600 dark:text-zinc-400">最近财报</div>
      {fins === undefined ? (
        <div className="space-y-2" aria-hidden>
          <Skeleton className="h-12 w-full rounded-lg" />
          <Skeleton className="h-12 w-full rounded-lg" />
        </div>
      ) : (
        <>
          {(fins ?? []).slice(0, 2).map((r) => (
            <div key={r.report_date} className="mb-1.5 rounded-lg border border-zinc-100 px-2 py-1.5 dark:border-zinc-800/60">
              <div className="flex justify-between">
                <span className="font-mono text-zinc-600 dark:text-zinc-400">{r.report_date}</span>
                <span className={`font-mono ${pctColor(r.profit_yoy)}`}>净利同比 {pctText(r.profit_yoy)}</span>
              </div>
              <div className="mt-0.5 flex justify-between text-zinc-600 dark:text-zinc-400">
                <span>
                  营收 <span className="font-mono text-zinc-800 dark:text-zinc-200">{r.revenue != null ? fmt(r.revenue / 1e8) : "--"}</span> 亿
                </span>
                <span>
                  归母净利 <span className="font-mono text-zinc-800 dark:text-zinc-200">{r.net_profit != null ? fmt(r.net_profit / 1e8) : "--"}</span> 亿
                </span>
              </div>
            </div>
          ))}
          {(fins ?? []).length === 0 && <p className="text-zinc-600 dark:text-zinc-400">暂无财报数据</p>}
        </>
      )}
    </div>
  );
}
