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

/** 亿元金额格式（无符号，表头/轴标签用）。 */
export function fmtYi(v: number | null | undefined, digits = 0): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "--";
  return v.toLocaleString("zh-CN", { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

/** 亿元金额带符号格式：+32.3亿 / -12.3亿 / --。
 *  正负只表达方向，颜色由调用方按 A 股惯例定（红涨绿跌）。 */
export function signedYi(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "--";
  return `${v >= 0 ? "+" : ""}${fmtYi(v, digits)}亿`;
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
  if (v === null || v === undefined || Number.isNaN(v) || v === 0) return "text-zinc-600 dark:text-zinc-400";
  return v > 0 ? "text-up-ink dark:text-up" : "text-down-ink dark:text-down";
}

export function pctText(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "--";
  return `${v > 0 ? "+" : ""}${fmt(v)}%`;
}

/**
 * 事件/快讯时间统一显示（2026-09-09 修「列表时间与详情时间不一致」）。
 *
 * 后端 event_card.published_at 全库统一为北京时间字符串
 * "YYYY-MM-DD HH:MM:SS.ffffff"（实测 400/400 条同格式，故按字符串排序即时间序）。
 * 这里**不 new Date() 解析**：非 ISO 字符串在各浏览器/时区下解析结果不一，
 * 正是列表与详情对不上的根源之一。纯字符串截取，列表与详情共用同一函数。
 */
export function eventTimeText(iso: string | null | undefined): string {
  if (!iso) return "--";
  const s = String(iso).replace("T", " ").trim();
  const m = s.match(/^\d{4}-(\d{2}-\d{2})[ ](\d{2}:\d{2})/);
  if (m) return `${m[1]} ${m[2]}`;
  return s.slice(0, 16) || "--";
}

export function timeText(iso: string | null | undefined): string {
  if (!iso) return "--";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "--";
  return d.toLocaleTimeString("zh-CN", { hour12: false });
}

/**
 * **agent 域专用**：把**无时区标记的 UTC 时间戳**正确显示为北京时间。
 *
 * 背景：库里 `agent_task` / `agent_agenda` / `agent_param_change` 等表存的是
 * `utcnow()` 的 **naive 值**（如 `2026-09-11T07:45:06.541226`，实际是 UTC 07:45
 * = 北京 15:45）。而 `new Date("...")` 对无时区标记的串按**运行环境时区**解释 ⇒
 * 在国内机器上会被当成北京时间 07:45，**整整早 8 小时**。
 *
 * 与 `timeText` 的两点差异（都不可省）：
 * 1. 无 `Z`/偏移的串**补 `Z` 按 UTC 解释**；已带时区的原样处理，两种输入都安全；
 * 2. 输出**固定 `Asia/Shanghai`**，不再依赖运行环境时区（`timeText` 没指定
 *    timeZone，在 CI/海外机器上同样会错，属既有隐患）。
 */
/** 把 agent 域的无时区 UTC 串解析成 Date；非法返回 null。 */
function parseUtcNaive(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  const s = String(iso).trim();
  const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/.test(s);
  const d = new Date(hasZone ? s : `${s}Z`);
  return Number.isNaN(d.getTime()) ? null : d;
}

export function timeTextUTC(iso: string | null | undefined): string {
  const d = parseUtcNaive(iso);
  if (!d) return "--";
  return d.toLocaleTimeString("zh-CN", { hour12: false, timeZone: "Asia/Shanghai" });
}

/** 同上，但带月日：`MM-DD HH:mm`（任务中心用的是这个格式）。 */
export function dateTimeTextUTC(iso: string | null | undefined): string {
  const d = parseUtcNaive(iso);
  if (!d) return "--";
  return d.toLocaleString("zh-CN", {
    hour12: false, timeZone: "Asia/Shanghai",
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  });
}

/* ---------------------------------------------------------------- 北京时间（Asia/Shanghai） */

/**
 * UTC ISO 时间戳 → 北京时间 `YYYY-MM-DD`；缺失/非法返回空串。
 *
 * **为什么必须显式指定时区**：后端时间戳统一是 UTC ISO（带 Z），而 `toLocaleDateString`
 * 默认按**运行环境时区**渲染。在非 +8 时区的环境（CI 容器、海外机器）直接用默认时区
 * 会把「09-11 早盘 09:20」显示成前一天，分钟决策、竞价、K 线实时合成全部错位一天。
 * 固定 `sv-SE` 是因为它天然输出 `YYYY-MM-DD`，无需手工拼零。
 *
 * 单点收口（2026-09-11 冗余清理）：此前 `lib/kline-live.ts` 与
 * `components/detail/minute-decision-panel.tsx` 各有一份逐字节同体实现。
 *
 * ⚠️ **例外**：`components/minute-chart.tsx` 保留一份手算偏移的 `bjHHMM`——那条路径按
 * 分钟点逐点调用（构建索引 + 每次 tick），Intl formatter 的构造开销高出约一个数量级。
 * 两者对 UTC 锚定的 ISO 输出一致，改动时**不要顺手"合并"那一份**。
 */
export function bjDate(ts: string | null | undefined): string {
  if (!ts) return "";
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleDateString("sv-SE", { timeZone: "Asia/Shanghai" });
}

/**
 * UTC ISO 时间戳 → 北京时间 `HH:MM`（分钟粒度，无秒）；缺失/非法返回空串。
 *
 * **非法日期必须返回空串，不能返回 `"Invalid Date"` 一类垃圾串**：调用方
 * （`lib/kline-live.ts` 的分时/K线实时合成）会用这个字符串比对「是否同一分钟」，
 * 两个非法时间戳若都返回同一段垃圾串就会「相等」，误判为同分钟而跳过合成。
 * 这是 `kline-live.test.ts` 跨分钟用例抓到的真实缺陷，改动时请保留空串语义。
 */
export function bjHHMM(ts: string | null | undefined): string {
  if (!ts) return "";
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleTimeString("sv-SE", { timeZone: "Asia/Shanghai", hour12: false }).slice(0, 5);
}

/** UTC ISO 时间戳 → 北京时间 `MM-DD`（不含年份，紧凑展示用）；缺失/非法返回空串。 */
export function bjMonthDay(ts: string | null | undefined): string {
  const d = bjDate(ts);
  return d ? d.slice(5) : "";
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

/**
 * 三态字面量 → 对外中文文案。**必须与后端 `picks/push_cards.py::_TRI_LABELS` 逐键一致**：
 * `unknown`（有判定但判不出）/ `none`（确实没有）/ `null`（字面量字符串 "null"）。
 *
 * S2-9（2026-09-11）：前端此前**漏了 `null` 键** ⇒ 后端若把字面量 `"null"` 送过来，
 * 飞书卡片显示「—」而界面直接打出 `null`（同一份数据两个端不一致）。
 * 注意占位符刻意不同源：`null` 用「—」（对齐后端），而真正缺数据用 `--`
 * （见 `triText` 的 `!v` 分支）——前者是"字段值是这四个字符"，后者是"压根没有值"。
 */
const TRI_LABELS: Record<string, string> = { unknown: "未判定", none: "无", null: "—" };

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
