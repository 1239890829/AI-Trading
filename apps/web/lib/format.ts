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
 * 胜率着色：`>= 50%` 正向、以下负向；**null / undefined 一律中性**。
 *
 * 三态纪律：胜率缺失（样本不足 / 未到期）是「未判定」，既不好也不坏。
 * 旧写法 `(rate ?? 0) >= 50` 把缺失当成 0% ⇒ 染成跌色，与同一容器里的
 * 「样本不足」文案自相矛盾——把"没数据"暗示成"表现差"。
 *
 * ⚠️ `scale` 必须由调用方显式声明：本仓胜率**两种口径并存且都属既成事实**——
 * `signal-health` / 角色胜率为 0-1 小数，`intraday-review` / 盘后复盘为 0-100 百分数
 * （见 `hunting/stats-bar.tsx` 顶部注释）。混用会把 0.55 误判为「低于 50%」，
 * 所以这里不开默认值，让口径差异在调用点可见。
 */
export function winRateColor(rate: number | null | undefined, scale: "01" | "pct"): string {
  if (rate === null || rate === undefined || Number.isNaN(rate)) return "text-zinc-600 dark:text-zinc-400";
  const v = scale === "01" ? rate * 100 : rate;
  return v >= 50 ? "text-up-ink dark:text-up" : "text-down-ink dark:text-down";
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
 * **agent 域专用**：把北京 naive 时间戳正确显示为北京时间。
 *
 * 背景（2026-09-12 方案 A 起）：agent 域 `agent_task` / `agent_agenda` /
 * `agent_param_change` 等表的事件时间已统一存**北京 naive**（`beijing_now_naive()`，
 * 与全系统事件时间口径一致；原 UTC naive 存量已一次性 +8h 迁移）。
 * 无时区标记的串 `new Date("...")` 会按**运行环境时区**解释 ⇒ 在海外/CI
 * 机器上错位，因此 naive 串必须补 `+08:00` 显式声明。
 *
 * 与 `timeText` 的两点差异（都不可省）：
 * 1. 无 `Z`/偏移的串**补 `+08:00` 按北京解释**；已带时区的原样处理，两种输入都安全；
 * 2. 输出**固定 `Asia/Shanghai`**，不再依赖运行环境时区（`timeText` 没指定
 *    timeZone，在 CI/海外机器上同样会错，属既有隐患）。
 */
/** 把 agent 域的时间串解析成 Date（naive 按北京解释）；非法返回 null。 */
function parseBjNaive(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  const s = String(iso).trim();
  const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/.test(s);
  const d = new Date(hasZone ? s : `${s}+08:00`);
  return Number.isNaN(d.getTime()) ? null : d;
}

export function timeTextBJ(iso: string | null | undefined): string {
  const d = parseBjNaive(iso);
  if (!d) return "--";
  return d.toLocaleTimeString("zh-CN", { hour12: false, timeZone: "Asia/Shanghai" });
}

/** 同上，但带月日：`MM-DD HH:mm`（任务中心用的是这个格式）。 */
export function dateTimeTextBJ(iso: string | null | undefined): string {
  const d = parseBjNaive(iso);
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
  // 通达信（TDX 直连，easy_tdx）。`tdx` = 逐笔主源（IMP-038）；`tdx_m1` = 分时降级备源
  // （`minute_backfill.tdx_minute_line_fallback`，此前无中文名，界面上直接露出裸 key）。
  tdx: "通达信",
  tdx_m1: "通达信",
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
 * invalid（非法）是持久态，稳定显示不闪。
 *
 * ⚠️ **本函数不回答"要不要渲染徽标"**——那是 `shouldShowQualityBadge` 的职责。
 * 09-02 的降噪目标只是 `low`/`medium` 两档，`high`（正常）**从未被决策过**；
 * 早期把本函数直接当作渲染门控，副作用是连「正常」一起隐掉，并让调用点各自
 * 判断、口径漂移（盘面指数卡显示「正常」而工作台不显示）。 */
export function isHardQuality(q: string): boolean {
  return q === "stale" || q === "invalid";
}

/**
 * 质量徽标**可见性策略的唯一决策点**（2026-09-14 口径统一）。
 *
 * 背景：此前 5 个调用点各自写判断，漂移成两派——`market/page.tsx` 无条件渲染
 * （显示「正常」），`index-cards.tsx` / `workbench/page.tsx` / `quote-strip.tsx`
 * 用 `isHardQuality` 门控（不显示）。同一「指数质量」概念、同一份数据，两个页面
 * 表现相反。
 *
 * 策略（2026-09-14 拍板）：
 * | 档位 | 是否渲染 | 理由 |
 * |---|---|---|
 * | `high` | ✅ 渲染为**静音灰**「正常」 | 隐藏它会让「正常」与「字段缺失」不可辨——三态必须可辨；且 `QualityBadge` 本为 `high` 定义了最静音样式（不加底色） |
 * | `medium` / `low` | ❌ 不渲染 | 盘中瞬态，下一拍即恢复 ⇒ 闪烁来源（09-02 修复对象） |
 * | `stale` / `invalid` | ✅ 常显 | 持久态 |
 * | 未知档位 | ✅ 渲染 | 宁可多显示异常，不可静默吞掉（新增档位不至于在界面消失） |
 *
 * 门控**由 `components/quality-badge.tsx` 内部承担**，调用点一律直接渲染
 * `<QualityBadge/>`，不得再自行加条件——否则本函数就白设了。
 */
export function shouldShowQualityBadge(q: string | null | undefined): boolean {
  if (!q) return false;
  return q !== "medium" && q !== "low";
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

/**
 * 金额 + 状态 → 「陈旧 / 降级 / 未判定」标记后缀（**文案单点**）。
 *
 * 为什么必须有这个后缀（IMP-002，2026-09-14）：后端五态语义里 `stale` / `degraded`
 * **按定义就是「有数据」**（见 `app/core/freshness.py` 状态表：`stale` = 有数据但超出
 * 新鲜窗口；`degraded` = 有数据但来自降级路径）。于是「有值」这条分支一旦不看 state，
 * `ready` 与 `stale` / `degraded` 就**渲染完全同形**——实测场景：成交额
 * `state=degraded` 且已陈旧 22 分钟时，界面与「实时」无差别，唯一的差别藏在 `title` 里。
 * 这是**红线 2（不得把过期缓存冒充实盘）在界面层的缺口**：后端诚实标了降级，
 * 前端把它擦掉了。
 *
 * `unknown`（无法判定，连时间戳都没有）同样要显式——按本仓三态纪律不得用默认值冒充判定
 * （`lib/api.ts::FreshnessState` docstring 与 `triText` 早已如此，此处补齐）。
 *
 * ⚠️ **键集必须覆盖后端五态全集**（`app/core/freshness.py::STATES`）——由
 * `backend/tests/test_cross_end_contract.py` 的「新鲜度状态→陈旧标记」契约守住。
 * 空串 = 「该状态不需要标记」。**不要**靠省略键来表达"不需要标记"：后端新增一个状态时，
 * 省略的键会静默变成"无标记"，界面与「实时」同形且不报错，
 * 正是 `IMP-002` 本项要堵的缺口形态（`?? q` 同族：缺键不报错，只是显示错了）。
 */
const TRI_AMOUNT_MARK: Record<string, string> = {
  ready: "",
  stale: "陈旧",
  degraded: "降级",
  unavailable: "",
  unknown: "未判定",
};

/**
 * 「带状态字段」的渲染单点：有值 → 金额（按 state 带标记）；未就绪 → 「加载中…」；
 * 真缺失 → `--`；无法判定 → 「未判定」。
 *
 * 为什么不能写成 `amount ? fmtAmount(amount) : "--"`：`null` 有**两种**成因——
 * ① 上游尚未就绪（冷启动 / 被限流，会自动恢复）；② 真的没有数据。
 * 原先两者在界面上完全同形，等于把「未判定」塌缩成「缺失」，用户无法分辨
 * "正在加载"还是"坏了"——实际报障：「两市成交额怎么没出来了」（2026-09-14）。
 *
 * 判据直接取后端 S2-1 契约的 `state`，**不另造**（`lib/api.ts::Freshness`）：
 * - 有值 ⇒ 金额；`stale` / `degraded` / `unknown` 三种**时效存疑**态追加标记后缀
 *   （IMP-002）。`unavailable` 与 `state` 未提供时不加标记：前者「无可用数据却给了值」
 *   本身自相矛盾，后者是**无从判定**（不假装标注）；
 * - 无值 + `unavailable`（尚未就绪）⇒ 「加载中…」；
 * - 无值 + `unknown`（有判定但判不出）⇒ 「未判定」。⚠️ **本行 2026-09-14 由
 *   「加载中…」改为「未判定」**：原实现把 `unknown` 与 `unavailable` 合并显示，
 *   与 `lib/api.ts` docstring 自述的纪律（「`unknown` 显式「未判定」」）及 `triText`
 *   的映射**互相矛盾**——同一份数据在飞书卡片与界面得到两个答案；
 * - 无值 + `ready` / `stale` / `degraded`（链路是通的）⇒ 确为缺失 `--`；
 * - `state` 未提供（`""`）⇒ 无从判定，保守显示 `--`，**不假装在加载**。
 *
 * ⚠️ 与 `triText` 同一条纪律：`--` 必须由**明确判定**得出，不能当兜底默认值。
 */
export function triAmount(amount: number | null | undefined, state?: string | null): string {
  const s = (state ?? "").trim().toLowerCase();
  if (amount !== null && amount !== undefined && !Number.isNaN(amount)) {
    const base = fmtAmount(amount);
    const mark = TRI_AMOUNT_MARK[s];
    return mark ? `${base}（${mark}）` : base;
  }
  if (s === "unavailable") return "加载中…";
  if (s === "unknown") return "未判定";
  return "--";
}
