/**
 * 交易时段与交易分钟轴（北京时间）—— 前端**唯一**口径来源。
 *
 * 非权威日历：只识别周末，不识别节假日。节假日被误判为盘中只是多打一次后端
 * 缓存（盘外分时数据不变），无害；反之交易日被误判为盘外会漏刷新。
 *
 * ## 两个意图，一份边界（2026-09-12 评审 R-2）
 *
 * 本模块承载**两种不同**的「是否盘中」判定，**不要合并成一个函数**——
 * 它们回答的是两个问题，合并必然给其中一个错答案：
 *
 * | 判定 | 口径 | 回答的问题 | 误判代价 |
 * |---|---|---|---|
 * | `isContinuousSession` | **严格** 09:30–11:30 / 13:00–15:00 | 此刻是否**真的**在连续竞价 | 用于 WS tick 合成分钟点；放宽会在集合竞价/午休**凭空造出一根不存在的点** |
 * | `isPollingSession` / `isTradingSession` | **宽松** 09:15–11:35 / 12:55–15:15 | 要不要按**盘中节奏轮询** | 宁可多打一次后端缓存，绝不能漏刷新 |
 *
 * 两者的边界**同源**：宽松区间由 `CONTINUOUS_SESSIONS` 派生（见 `POLL_PAD`）
 * ——此前是两处各写一份区间（`kline-live.ts` 的 09:30–11:30/13:00–15:00
 * 与这里的 09:15–11:35/12:55–15:15），改一处不会带动另一处。
 */
export const TRADING_MINUTES = 240; // 一个交易日的交易分钟数（09:30–11:30 + 13:00–15:00）

/** 北京分钟数边界（当日 00:00 起算）。数值本身无意义，**名字**才是口径。 */
const MIN = {
  auction: 9 * 60 + 15, // 09:15 集合竞价开始
  open: 9 * 60 + 30, // 09:30 连续竞价开始
  morningClose: 11 * 60 + 30, // 11:30 上午连续竞价结束
  noonEnd: 13 * 60, // 13:00 下午连续竞价开始
  close: 15 * 60, // 15:00 收盘
} as const;

/** **严格**口径：连续竞价时段（闭区间，北京分钟数）。 */
export const CONTINUOUS_SESSIONS: readonly (readonly [number, number])[] = [
  [MIN.open, MIN.morningClose],
  [MIN.noonEnd, MIN.close],
];

/**
 * 宽松口径相对严格口径的松弛量（分钟）。
 * 单列成常量只为让「宽松 = 严格 ± 若干分钟」这层关系可读且**机械成立**。
 */
const POLL_PAD = {
  head: 15, // 09:30 − 15 = 09:15（集合竞价）
  morningTail: 5, // 11:30 + 5
  noonHead: 5, // 13:00 − 5
  tail: 15, // 15:00 + 15（收盘缓冲）
} as const;

/** **宽松**口径：轮询节奏时段（由 `CONTINUOUS_SESSIONS` 派生，勿手写）。 */
export const POLLING_SESSIONS: readonly (readonly [number, number])[] = [
  [MIN.open - POLL_PAD.head, MIN.morningClose + POLL_PAD.morningTail],
  [MIN.noonEnd - POLL_PAD.noonHead, MIN.close + POLL_PAD.tail],
];

function inRanges(min: number, ranges: readonly (readonly [number, number])[]): boolean {
  return ranges.some(([from, to]) => min >= from && min <= to);
}

/** **严格**：此刻是否在连续竞价（入参 = 北京当日分钟数）。 */
export function isContinuousSession(min: number): boolean {
  return inRanges(min, CONTINUOUS_SESSIONS);
}

/** **宽松**：是否按盘中节奏轮询（入参 = 北京当日分钟数）。 */
export function isPollingSession(min: number): boolean {
  return inRanges(min, POLLING_SESSIONS);
}

/**
 * 任意 `Date` → **北京墙钟**的 `Date`（其本机 getter 读出的就是北京值）。
 *
 * 换算方式：`本机偏移 + 480 分钟` 平移。中国无夏令时、恒为 UTC+8，故这是精确换算，
 * 且**与运行机器的时区无关**（在 UTC+0 机器上平移 480，在 UTC+8 机器上平移 0，
 * 两种情形下 `getHours()` 都返回北京墙钟）。
 *
 * 这里是前端「当前北京时间」的唯一实现——对应后端 `app/core/bjtime.py`。
 */
function bjWallClock(now: Date): Date {
  return new Date(now.getTime() + (now.getTimezoneOffset() + 480) * 60_000);
}

/** 北京当日分钟数（00:00 起算）。`isContinuousSession` 一类判定吃这个值。 */
export function bjMinuteOfDay(now: Date = new Date()): number {
  const bj = bjWallClock(now);
  return bj.getHours() * 60 + bj.getMinutes();
}

/**
 * 北京日期 `YYYY-MM-DD`（对应后端 `bjtime.beijing_today()`）。
 *
 * ⚠️ **不要用 `new Date()` 的本地 `getFullYear/getMonth/getDate`**：那给的是运行机器
 * 的日期，跨零点会与交易所日期差一天，而交易日归属是**北京**口径。
 */
export function bjToday(now: Date = new Date()): string {
  const bj = bjWallClock(now);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${bj.getFullYear()}-${p(bj.getMonth() + 1)}-${p(bj.getDate())}`;
}

/**
 * 宽松口径的 `Date` 门面（供轮询/降频决策调用）。
 */
export function isTradingSession(now: Date = new Date()): boolean {
  const bj = bjWallClock(now);
  const day = bj.getDay();
  if (day === 0 || day === 6) return false;
  return isPollingSession(bjMinuteOfDay(now));
}

/**
 * 北京 `HH:MM` → 交易分钟序（0..240 轴）：09:30=0 … 11:30=120 …（午休折叠）… 13:00=120 … 15:00=240。
 *
 * **与后端 `_sina_bar_seq`（`app/market/fund_flow.py:101`）同口径**——改一侧必须同步另一侧。
 * 后端对午休与收盘外**返回 None**（那类点根本不产出），前端这里把它们折叠到相邻边界。
 *
 * ⚠️ 午休折叠**不是可有可无**（2026-09-12 评审 R-3 实测发现）：旧实现在
 * `components/market/flow-intraday-chart.tsx` 内写作
 *
 * ```ts
 * if (hm <= 570) return 0;
 * if (hm <= 690) return hm - 570;
 * return Math.min(240, 120 + (hm - 780));   // ← 隐含假设 hm ≥ 13:00
 * ```
 *
 * 第三行只在 `hm ≥ 13:00` 成立，对 11:31–12:59 会算出**倒退**的序
 * （11:31→31、12:00→60，而 11:30→120），在资金流曲线上表现为折线往回画。
 * 历史数据下后端已过滤午休点故未暴露，但那是**隐式契约**——前端没有守卫。
 * 这里显式折叠到 120，保证整日**单调不减**（有测试钉住）。
 *
 * 非法输入（空串 / "NaN"）返回 0：旧实现返回 `NaN`，会原样写进 SVG 的 `d` 属性。
 */
export function tradingSeqFromHHMM(hhmm: string): number {
  const h = Number(hhmm.slice(0, 2));
  const m = Number(hhmm.slice(3, 5));
  if (!Number.isFinite(h) || !Number.isFinite(m)) return 0;
  const hm = h * 60 + m;
  if (hm <= MIN.open) return 0; // ≤09:30 → 0（含 09:15–09:25 集合竞价）
  if (hm <= MIN.morningClose) return hm - MIN.open; // 09:30–11:30 → 0..120
  if (hm < MIN.noonEnd) return 120; // 午休 11:31–12:59 → 折叠到上午收盘位
  return Math.min(TRADING_MINUTES, 120 + (hm - MIN.noonEnd)); // 13:00–15:00 → 120..240
}
