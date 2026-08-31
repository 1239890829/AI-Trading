/**
 * 新闻/公告 → K 线事件点（P1-8）。
 *
 * 数据源 = 详情面板已有的 digest 结果（anns/news 状态），零新增请求。
 * 对齐规则：条目日期取前 10 位（兼容 "2026-08-28 16:00:00" 形态），只保留
 * 恰好落在 K 线 bar 日期上的事件——非交易日披露不做顺延（顺延等于臆测归属日，
 * 原文日期以资讯页签为准）。
 * 同日同类型多条 → 合并为一个点（图上 marker 只画类型不画标题，避免刷屏；
 * 详情看资讯页签）。任一条目重要度为「高」则整点标记 important。
 */

export interface EventMark {
  /** YYYY-MM-DD，已对齐 K 线 bar 日期 */
  date: string;
  kind: "公告" | "新闻";
  /** 合并后的标题（供悬停提示与调试；图上只画类型） */
  title: string;
  /** 任一条目重要度为「高」 */
  important: boolean;
}

interface InfoLike {
  title: string;
  date?: string | null;
  importance?: string | null;
}

export function buildEventMarks(
  barDates: string[],
  anns?: InfoLike[] | null,
  news?: InfoLike[] | null,
): EventMark[] {
  const dateSet = new Set(barDates);

  const merge = (items: InfoLike[] | null | undefined, kind: EventMark["kind"]): Map<string, EventMark> => {
    const byDate = new Map<string, EventMark>();
    for (const it of items ?? []) {
      const d = (it.date ?? "").slice(0, 10);
      if (!d || !dateSet.has(d)) continue;
      const important = it.importance === "高";
      const prev = byDate.get(d);
      if (prev) {
        prev.important = prev.important || important;
        prev.title = `${prev.title} ／ ${it.title}`;
      } else {
        byDate.set(d, { date: d, kind, title: it.title, important });
      }
    }
    return byDate;
  };

  const out = [...merge(anns, "公告").values(), ...merge(news, "新闻").values()];
  out.sort((a, b) => a.date.localeCompare(b.date));
  return out;
}
