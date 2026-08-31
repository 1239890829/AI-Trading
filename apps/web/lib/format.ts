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
