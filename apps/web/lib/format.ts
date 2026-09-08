export function fmt(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "--";
  return v.toLocaleString("zh-CN", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

/**
 * 解析用户输入的数字。
 *
 * 输入框回填的是 fmt() 的结果（zh-CN 千分位），直接 parseFloat("1,297.40")
 * 会在逗号处截断成 1 —— 下单价因此变成 1 元。所有读回数字的地方都走这里。
 */
export function parseNum(s: string | number | null | undefined): number {
  if (typeof s === "number") return Number.isFinite(s) ? s : 0;
  if (s === null || s === undefined || s === "") return 0;
  const n = parseFloat(String(s).replace(/,/g, ""));
  return Number.isFinite(n) ? n : 0;
}

export function fmtAmount(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "--";
  if (Math.abs(v) >= 1e8) return `${fmt(v / 1e8)} 亿`;
  if (Math.abs(v) >= 1e4) return `${fmt(v / 1e4)} 万`;
  return fmt(v, 0);
}

export function fmtVolume(v: number | null | undefined): string {
  // 后端统一为股；展示层转手
  if (v === null || v === undefined || Number.isNaN(v)) return "--";
  return fmt(v / 100, 0);
}

/** 热度值压缩展示（ths 人气为无量纲计数，如 6002184 → 600.2万）。 */
export function fmtHeat(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "--";
  if (Math.abs(v) >= 1e8) return `${fmt(v / 1e8, 2)}亿`;
  if (Math.abs(v) >= 1e4) return `${fmt(v / 1e4, 1)}万`;
  return fmt(v, 0);
}

export function pctColor(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v) || v === 0) return "text-zinc-400";
  return v > 0 ? "text-up" : "text-down";
}

export function pctText(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "--";
  return `${v > 0 ? "+" : ""}${fmt(v)}%`;
}

export function timeText(iso: string | null | undefined): string {
  if (!iso) return "--";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "--";
  return d.toLocaleTimeString("zh-CN", { hour12: false });
}

const SOURCE_LABELS: Record<string, string> = {
  ths: "同花顺",
  tencent: "腾讯",
  eastmoney: "东方财富",
  sina: "新浪",
  mock: "演示数据",
};

/** 数据源 key → 中文名。后端返回的是 provider 内部标识（如 tencent），不可直接展示。 */
export function sourceLabel(s: string | null | undefined): string {
  if (!s) return "--";
  return SOURCE_LABELS[s] ?? s;
}

export function qualityLabel(q: string): string {
  const map: Record<string, string> = {
    high: "正常",
    medium: "延迟",
    low: "可疑",
    stale: "过期",
    invalid: "非法",
  };
  return map[q] ?? q;
}

/**
 * 是否值得渲染质量徽标的"硬"质量问题（2026-09-02 用户反馈"偶尔弹出可疑标签"）：
 * low（可疑）/medium（延迟）来自盘中源字段瞬时不同步（如价格已更新而涨跌幅
 * 滞后一拍触发 change_pct_mismatch 判 low），下一个 tick 即恢复 high——徽标
 * 一秒弹现又消失，正是闪烁来源。这两档不再上界面；stale（过期/休市）与
 * invalid（非法）是持久态，稳定显示不闪。 */
export function isHardQuality(q: string): boolean {
  return q === "stale" || q === "invalid";
}

const TRI_LABELS: Record<string, string> = { unknown: "未判定", none: "无" };

/**
 * 三态判定字段的对外文案（与后端 push_cards.tri_text 同语义，2026-09-08 修复）。
 *
 * `null/undefined/空` = 缺数据 → "--"；`unknown` = 有判定但判不出 → "未判定"
 * （判不出 ≠ 低）；其余原样。历史 bug：`level || "—"` 兜不住 unknown——非空
 * 字符串是 truthy，字面量 "unknown" 被直接显示在界面与飞书卡片上。
 */
export function triText(v: string | null | undefined): string {
  if (!v) return "--";
  const s = v.trim();
  if (!s) return "--";
  return TRI_LABELS[s.toLowerCase()] ?? s;
}
