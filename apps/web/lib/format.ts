export function fmt(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "--";
  return v.toLocaleString("zh-CN", { minimumFractionDigits: digits, maximumFractionDigits: digits });
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
