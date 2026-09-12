/**
 * 通知已读状态（单一实现，纯函数 + localStorage 读写）。
 *
 * ## 为什么单独抽出来
 * 原实现把「已读水位」直接存成**字符串**再做**字面比较**，而两个写入方用了
 * **两种格式 / 两个时区**：
 *  - `markAllRead()` 写 `new Date().toISOString()` → `2026-09-11T04:31:38.000Z`（UTC + T 分隔）
 *  - 打开抽屉时写 `payload.generated_at` → `2026-09-11 11:00:00`（北京 naive + 空格分隔）
 * 条目 `ts` 则恒为北京 naive（`2026-09-11 12:35:00`）。于是
 * `"2026-09-11 12:35:00" > "2026-09-11T04:31:38.000Z"` **恒为 false**（第 11 位 `' '` < `'T'`），
 * 当天所有条目都被当成"已读"；一旦水位变成另一格式或跨了日期，又会**整天条目一起**
 * 计入未读 —— 用户看到的就是「一键已读后计数没了，来了新的却是在之前的累积上累加」。
 *
 * 结论：**时间一律先转成 epoch 毫秒再比大小**，永不比较字符串。
 *
 * ## 已读语义（两层）
 *  - `seenBefore`：水位（epoch）。`ts <= seenBefore` 视为已读 —— 「全部已读」推进它。
 *  - `readIds`：水位之后被**单独点开**的条目 id —— 逐条已读。
 *  两层叠加后，未读 = 既晚于水位、又不在 readIds 里的条目。
 *
 * ## 旧数据迁移
 * 旧键（`lastSeenTs` / `clearBeforeTs`）在首次读取时解析成 epoch 后写入新键，
 * 旧键删除。旧值若是 UTC ISO 也能被 `parseTs` 正确处理 —— 迁移顺带修掉历史错值。
 *
 * ## 持久化分层（2026-09-12）
 * localStorage 是**首帧缓存**，服务端（`/api/notifications/read-state`）才是权威。
 * 原因：localStorage 按 origin 命名空间，换源/换 profile/清站点数据即整体归零
 * （实测 localhost↔127.0.0.1 两份存储互不可见，徽标回到 65）。两侧按**单调规则**
 * 合并，详见文件尾部「服务端持久化」一节。
 */

import { getNotificationReadState, saveNotificationReadState } from "@/lib/api";

export interface ReadState {
  /** 已读水位（epoch ms）：ts ≤ 水位的条目已读 */
  seenBefore: number;
  /** 水位之后被单独点开的条目 id */
  readIds: string[];
}

export interface NotificationLike {
  id: string;
  ts: string | null;
}

export const READ_KEY = "ashare.notifications.read";
export const CLEAR_KEY = "ashare.notifications.clear";
/** 旧键（迁移来源，读一次即删） */
export const LEGACY_LAST_SEEN_KEY = "ashare.notifications.lastSeenTs";
export const LEGACY_CLEAR_BEFORE_KEY = "ashare.notifications.clearBeforeTs";

/** 北京无夏令时，固定 UTC+8；`Date.UTC` 允许小时为负并自动归一化。 */
const BJ_OFFSET_HOURS = 8;

const NAIVE_RE =
  /^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2})(?::(\d{2}))?(?:\.(\d{1,9}))?)?$/;
/** 带时区标记（Z / ±HH:MM / ±HHMM）→ 是绝对时刻，交给 Date.parse。 */
const ABSOLUTE_RE = /(?:Z|[+-]\d{2}:?\d{2})$/i;

/**
 * 通知时间戳 → epoch 毫秒；无法解析返回 `null`（**不返回 0**，0 会被误当"很久以前"）。
 *
 * 支持：`YYYY-MM-DD HH:MM:SS`（北京 naive，后端 `ts` 的实际格式）、
 * `YYYY-MM-DD HH:MM:SS.ssssss`（**后端 alert 的实际格式，带微秒**）、
 * `YYYY-MM-DD HH:MM`、`YYYY-MM-DD`、以及带 Z/偏移的 ISO（旧键与 `Date.toISOString()`）。
 *
 * ⚠️ 小数位必须吃下 **1–9 位**（2026-09-12 修复）：后端 alert 的 `ts` 来自
 * `datetime.isoformat(sep=" ")`，**带 6 位微秒**（实测 `2026-09-11 14:07:24.569986`）。
 * 旧正则只允许 1–3 位 → 这些时间戳落到 `Date.parse` 兜底，而 `Date.parse`
 * 把无时区标记的串按**浏览器本地时区**解释：在 Asia/Shanghai 下恰好等价（实测 delta=0ms），
 * 但换任何非 +8 时区就会整体偏移 —— 偏移到"未来"时那些条目**永远算未读**
 * （水位由 `Date.now()` 推进，永远追不上）；偏移到"过去"则刚到的提醒被当成已读。
 * 本模块的整个前提是"北京 naive 语义"，因此**不能让任何一条路径偷偷依赖浏览器时区**。
 */
export function parseTs(ts: string | null | undefined): number | null {
  if (!ts) return null;
  const s = ts.trim();
  if (!s) return null;
  if (ABSOLUTE_RE.test(s)) {
    const ms = Date.parse(s);
    return Number.isNaN(ms) ? null : ms;
  }
  const m = NAIVE_RE.exec(s);
  if (m) {
    const [, y, mo, d, h, mi, sec, msPart] = m;
    // 微秒截断到毫秒（3 位，右侧补零）：569986 → 569
    const ms = msPart ? Number(msPart.slice(0, 3).padEnd(3, "0")) : 0;
    // 北京墙钟 → UTC 时刻：小时减 8（可为负，Date.UTC 会归一化到前一天）
    return Date.UTC(
      Number(y),
      Number(mo) - 1,
      Number(d),
      Number(h ?? 0) - BJ_OFFSET_HOURS,
      Number(mi ?? 0),
      Number(sec ?? 0),
      ms,
    );
  }
  const fallback = Date.parse(s);
  return Number.isNaN(fallback) ? null : fallback;
}

function storage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null; // 隐私模式 / 禁 cookie：降级为「无已读状态」，绝不抛
  }
}

export function loadReadState(store: Storage | null = storage()): ReadState {
  const empty: ReadState = { seenBefore: 0, readIds: [] };
  if (!store) return empty;
  try {
    const raw = store.getItem(READ_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as Partial<ReadState>;
      return {
        seenBefore:
          typeof parsed.seenBefore === "number" && Number.isFinite(parsed.seenBefore)
            ? parsed.seenBefore
            : 0,
        readIds: Array.isArray(parsed.readIds) ? parsed.readIds.map(String) : [],
      };
    }
    // 迁移：旧键是「时间字符串」，解析成 epoch 后落新键（顺带修正格式错值）
    const legacy = store.getItem(LEGACY_LAST_SEEN_KEY);
    if (legacy) {
      const state: ReadState = { seenBefore: parseTs(legacy) ?? 0, readIds: [] };
      saveReadState(state, store);
      store.removeItem(LEGACY_LAST_SEEN_KEY);
      return state;
    }
    return empty;
  } catch {
    return empty;
  }
}

export function saveReadState(state: ReadState, store: Storage | null = storage()): void {
  if (!store) return;
  try {
    store.setItem(READ_KEY, JSON.stringify(state));
  } catch {
    /* 配额/隐私模式：状态丢失不影响读取 */
  }
}

/** 清除水位（epoch）：早于它的条目整体隐藏（「一键清除」）。0 = 未清除过。 */
export function loadClearBefore(store: Storage | null = storage()): number {
  if (!store) return 0;
  try {
    const raw = store.getItem(CLEAR_KEY);
    if (raw) {
      const n = Number(raw);
      if (Number.isFinite(n)) return n;
    }
    const legacy = store.getItem(LEGACY_CLEAR_BEFORE_KEY);
    if (legacy) {
      const n = parseTs(legacy) ?? 0;
      saveClearBefore(n, store);
      store.removeItem(LEGACY_CLEAR_BEFORE_KEY);
      return n;
    }
    return 0;
  } catch {
    return 0;
  }
}

export function saveClearBefore(ms: number, store: Storage | null = storage()): void {
  if (!store) return;
  try {
    store.setItem(CLEAR_KEY, String(ms));
  } catch {
    /* 同上 */
  }
}

/** 条目是否被「一键清除」挡住（隐藏）。ts 缺失时按未清除处理（不隐藏）。 */
export function isCleared(item: NotificationLike, clearBefore: number): boolean {
  if (!clearBefore) return false;
  const t = parseTs(item.ts);
  return t !== null && t < clearBefore;
}

/** 未读判定：ts 晚于已读水位，且没被单独读过。ts 缺失一律算未读（宁可多提醒）。 */
export function isUnread(item: NotificationLike, state: ReadState): boolean {
  if (state.readIds.includes(item.id)) return false;
  const t = parseTs(item.ts);
  if (t === null) return true;
  return t > state.seenBefore;
}

/** 未读数（已清除的条目不计入）。 */
export function countUnread(
  items: readonly NotificationLike[],
  state: ReadState,
  clearBefore = 0,
): number {
  let n = 0;
  for (const i of items) {
    if (isCleared(i, clearBefore)) continue;
    if (isUnread(i, state)) n += 1;
  }
  return n;
}

/** 「全部已读」：水位推进到 `now`，并清空逐条已读表（水位已覆盖一切更早条目）。 */
export function withAllRead(state: ReadState, now: number = Date.now()): ReadState {
  return { seenBefore: Math.max(state.seenBefore, now), readIds: [] };
}

/** 逐条已读：只在水位未覆盖时登记 id，避免 readIds 无限增长。 */
export function withRead(
  state: ReadState,
  item: NotificationLike,
  now: number = Date.now(),
): ReadState {
  const t = parseTs(item.ts);
  if (t !== null && t <= state.seenBefore) return state;
  if (state.readIds.includes(item.id)) return state;
  return { seenBefore: state.seenBefore, readIds: [...state.readIds, item.id] };
}

/* ------------------------------------------------------------------ 外部存储订阅
 *
 * 为什么用 `useSyncExternalStore` 而不是 `useState + useEffect`：
 *  1. localStorage 就是"外部存储"，这正是该 API 的用途；
 *  2. `useState(loadReadState)` 会在服务端/客户端渲染出**不同**的初始值 →
 *     hydration 不一致；effect 里补读则被 `react-hooks/set-state-in-effect` 拦下
 *     （本项目 eslint 基线是 0 error / 0 warn，不新增豁免）；
 *  3. 该 API 在 hydration 阶段用 `getServerSnapshot()`（空状态），
 *     hydration 后自动切到真实快照并重渲染 —— 两端首帧一致、数据也不丢。
 *
 * 快照必须是**引用稳定**的（同一状态返回同一对象），否则 useSyncExternalStore
 * 会无限重渲染；因此这里做模块级缓存，只有变更时才换引用。
 */

export interface PrefsSnapshot {
  read: ReadState;
  /** 清除水位（epoch）：早于它的条目整体隐藏 */
  clearBefore: number;
}

const EMPTY_SNAPSHOT: PrefsSnapshot = { read: { seenBefore: 0, readIds: [] }, clearBefore: 0 };

let cache: PrefsSnapshot | null = null;
const listeners = new Set<() => void>();

export function getPrefsSnapshot(): PrefsSnapshot {
  if (cache === null) {
    cache = { read: loadReadState(), clearBefore: loadClearBefore() };
  }
  return cache;
}

/** 服务端/hydration 首帧：空状态（服务端没有 localStorage）。 */
export function getServerPrefsSnapshot(): PrefsSnapshot {
  return EMPTY_SNAPSHOT;
}

export function subscribePrefs(cb: () => void): () => void {
  listeners.add(cb);
  // 首个订阅者建立时拉一次服务端权威状态（2026-09-12）。
  // 放在这里而不是组件的 effect 里：`subscribe` 由 React 在挂载后的被动阶段调用
  // （不在渲染期），且"有人在听"正是需要权威状态的时刻；组件侧因此零改动。
  void hydratePrefsFromServer();
  const onStorage = (e: StorageEvent) => {
    // 多标签页同步：别处改了已读 → 清缓存后通知本页重算（不写 storage）
    const relevant = !e.key || e.key === READ_KEY || e.key === CLEAR_KEY;
    if (relevant) {
      cache = null;
      cb();
    }
  };
  try {
    window.addEventListener("storage", onStorage);
  } catch {
    /* 非浏览器环境 */
  }
  return () => {
    listeners.delete(cb);
    try {
      window.removeEventListener("storage", onStorage);
    } catch {
      /* 同上 */
    }
  };
}

/** 写入并广播（唯一的变更入口；落盘 + 换引用 + 通知订阅者 + 回写服务端）。 */
export function setPrefs(next: PrefsSnapshot): void {
  cache = next;
  saveReadState(next.read);
  saveClearBefore(next.clearBefore);
  for (const l of listeners) l();
  // 服务端回写（fire-and-forget）：hydration 未完成或正在采纳服务端值时跳过，
  // 见模块尾部「服务端持久化」一节的闸门说明。
  if (remoteEnabled && !suppressPush) void pushRemote(next);
}

/** 只测试用：清掉模块级缓存（避免跨用例串状态）。 */
export function __resetPrefsCache(): void {
  cache = null;
  listeners.clear();
  remoteEnabled = false;
  hydrating = false;
}

/* ------------------------------------------------------------------ 服务端持久化（2026-09-12 缺陷修复）
 *
 * ## 为什么 localStorage 不够
 * 已读状态此前**只**存在 localStorage，而它是**按 origin 命名空间**的：
 * `http://localhost:3000` 与 `http://127.0.0.1:3000` 是两份互不可见的存储；
 * 换端口、换浏览器 profile、内嵌预览分区、隐私模式、清站点数据 — 任意一种都会让
 * 已读状态**整体归零**。实测复现：同一浏览器里 localhost 侧点过「全部已读」，
 * 切到 127.0.0.1 侧徽标立刻回到 **65**（== 通知总数），与用户报告的
 * 「重启后全部变未读、显示 65 条未读」完全一致。
 *
 * ## 分工
 * - **服务端** = 权威状态（`/api/notifications/read-state`，落 SQLite）；
 * - **localStorage** = 首帧快速缓存（同步可读，避免"先显示 0 未读再跳成 65"的闪动）。
 *
 * ## 合并必须单调（与服务端 `merge_read_state` 同规则）
 * 水位取大、清除水位取大、逐条 id 取并集。**任何一侧领先都不会把另一侧已读的条目
 * 变回未读** —— 这正是本次缺陷的形态，前端的合并方向也不能错。
 *
 * ## 何时不用网络
 * `hydrating` / `remoteEnabled` 双闸：hydration 失败或未跑过（例如单测环境）时，
 * 回写整体关闭，本地缓存照常工作 —— 服务端不可达只影响"能不能跨源恢复"，
 * 不影响"当前这个源里能不能记已读"。
 */

/** 与服务端 `READ_IDS_MAX` 一致：逐条已读只登记水位之后的条目，正常情况下不会撞上界。 */
export const READ_IDS_MAX = 500;

export interface RemoteReadState {
  seenBefore: number;
  readIds: string[];
  clearBefore: number;
}

/** 单调合并（纯函数）。顺序：并集去重 + 只保留尾部（较新登记的一批）。 */
export function mergeReadState(a: RemoteReadState, b: RemoteReadState): RemoteReadState {
  const seenIds = new Set<string>();
  const readIds: string[] = [];
  for (const id of [...a.readIds, ...b.readIds]) {
    if (id && !seenIds.has(id)) {
      seenIds.add(id);
      readIds.push(id);
    }
  }
  return {
    seenBefore: Math.max(a.seenBefore || 0, b.seenBefore || 0),
    clearBefore: Math.max(a.clearBefore || 0, b.clearBefore || 0),
    readIds: readIds.length > READ_IDS_MAX ? readIds.slice(-READ_IDS_MAX) : readIds,
  };
}

function localState(snapshot: PrefsSnapshot): RemoteReadState {
  return {
    seenBefore: snapshot.read.seenBefore,
    readIds: snapshot.read.readIds,
    clearBefore: snapshot.clearBefore,
  };
}

function sameState(a: RemoteReadState, b: RemoteReadState): boolean {
  return (
    a.seenBefore === b.seenBefore &&
    a.clearBefore === b.clearBefore &&
    a.readIds.length === b.readIds.length &&
    a.readIds.every((id, i) => id === b.readIds[i])
  );
}

/** hydration 是否已完成（决定要不要回写服务端；单测里保持 false ⇒ 零网络）。 */
let remoteEnabled = false;
/** 防重入：React StrictMode 会 subscribe 两次，只允许发一次拉取。 */
let hydrating = false;
/** 采纳服务端回传值时抑制回推，避免"推-采纳-再推"自激。 */
let suppressPush = false;

function applyState(next: RemoteReadState): void {
  suppressPush = true;
  try {
    setPrefs({ read: { seenBefore: next.seenBefore, readIds: next.readIds }, clearBefore: next.clearBefore });
  } finally {
    suppressPush = false;
  }
}

async function pushRemote(snapshot: PrefsSnapshot): Promise<void> {
  try {
    const sent = localState(snapshot);
    // 线上载荷是 snake_case（与本文件其余通知接口一致）→ 在边界显式转换
    const saved = await saveNotificationReadState({
      seen_before: sent.seenBefore,
      read_ids: sent.readIds,
      clear_before: sent.clearBefore,
    });
    const back: RemoteReadState = {
      seenBefore: saved.seen_before,
      readIds: saved.read_ids,
      clearBefore: saved.clear_before,
    };
    // 服务端可能更靠前（另一标签页推进过）→ 采纳一次即可，不再回推
    if (!sameState(back, sent)) {
      const cur = getPrefsSnapshot();
      const merged = mergeReadState(localState(cur), back);
      if (!sameState(merged, localState(cur))) applyState(merged);
    }
  } catch {
    /* 服务端不可达：本地缓存继续工作，下次变更再同步（不弹错、不阻塞 UI） */
  }
}

/** 拉取权威状态并与本地单调合并；本地更靠前则回推（首次上线时把存量已读带上去）。 */
export async function hydratePrefsFromServer(): Promise<void> {
  if (remoteEnabled || hydrating || typeof window === "undefined") return;
  hydrating = true;
  try {
    const remote = await getNotificationReadState();
    const back: RemoteReadState = {
      seenBefore: remote.seen_before,
      readIds: remote.read_ids,
      clearBefore: remote.clear_before,
    };
    remoteEnabled = true;
    const cur = getPrefsSnapshot();
    const merged = mergeReadState(localState(cur), back);
    if (!sameState(merged, localState(cur))) applyState(merged);
    if (!sameState(merged, back)) void pushRemote(getPrefsSnapshot());
  } catch {
    /* 保持 remoteEnabled = false：本地照常可用，下次订阅再试 */
  } finally {
    hydrating = false;
  }
}
