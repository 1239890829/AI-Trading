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

/**
 * 当日新闻 → 分时图分钟级事件点（ui-redesign §2「新闻/公告图上事件点」的分时半边）。
 *
 * 数据源同 buildEventMarks（digest 的 news，零新增请求）。对齐规则：
 * - 只有新闻带分钟精度（"YYYY-MM-DD HH:MM"），公告只有日期——公告不进分时
 *   （硬标到某分钟等于臆造发布时刻，公告归属见日 K 琥珀点 + 资讯页签）；
 * - 只保留日期恰为分时日（bjDate）的事件，且时刻必须落在图上真实存在的
 *   槽区间（09:25 竞价 / 09:30-11:30 / 13:00-15:00）——盘前/午休/盘后发布的
 *   新闻不顺延（顺延=臆测归属分钟），原文时间以资讯页签为准；
 * - 同一分钟多条合并为一个点（count 计数），任一条目重要度「高」则整点 important。
 * 返回按时间升序的数组（下游画点 + tooltip 查表）。
 */
export interface MinuteNewsEvent {
  /** 北京墙上钟 "HH:MM"，已确认落在分时图槽区间 */
  hhmm: string;
  /** 合并后的标题（tooltip 显示，超长由展示层截断） */
  title: string;
  /** 同分钟合并的条数 */
  count: number;
  /** 任一条目重要度为「高」 */
  important: boolean;
}

/** 分时图真实存在的槽时刻（与 minute-chart.tsx slotSecs 一致） */
const MINUTE_SLOTS: ReadonlySet<string> = (() => {
  const s = new Set<string>(["09:25"]);
  for (let m = 570; m <= 690; m++) s.add(`${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`);
  for (let m = 780; m <= 900; m++) s.add(`${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`);
  return s;
})();

export function buildMinuteNewsEvents(
  news?: InfoLike[] | null,
  points?: { ts: string }[] | null,
): MinuteNewsEvent[] {
  if (!news || news.length === 0 || !points || points.length === 0) return [];
  // 分时日的北京日期（与 minute-chart.tsx 同一换算：真实 ts + 8h 的 UTC 日期）
  const bjDate = new Date(new Date(points[0].ts).getTime() + 8 * 3600 * 1000).toISOString().slice(0, 10);

  const byHHMM = new Map<string, MinuteNewsEvent>();
  for (const it of news) {
    const d = it.date ?? "";
    if (d.slice(0, 10) !== bjDate) continue;
    const hhmm = d.slice(11, 16);
    if (!MINUTE_SLOTS.has(hhmm)) continue; // 盘前/午休/盘后/残缺时刻：不顺延
    const prev = byHHMM.get(hhmm);
    if (prev) {
      prev.count += 1;
      prev.title = `${prev.title} ／ ${it.title}`;
      prev.important = prev.important || it.importance === "高";
    } else {
      byHHMM.set(hhmm, { hhmm, title: it.title, count: 1, important: it.importance === "高" });
    }
  }
  return [...byHHMM.values()].sort((a, b) => a.hhmm.localeCompare(b.hhmm));
}
