/**
 * AI 助手会话历史（localStorage 单一实现，纯函数 + 存储读写分离）。
 *
 * ## 为什么单独抽出来
 * 会话内容此前只活在 `floating-assistant.tsx` 的 `useState` 里 —— **刷新即失**
 * （软导航不丢只是因为浮窗挂在 `layout` 上）。本模块把「存几条、怎么裁剪、
 * 数据坏了怎么办、写不下怎么办」从组件里拿出来；组件只负责渲染与事件。
 * 判据（哪些消息该留、标题取哪句）全部落在这里，便于单独钉死。
 *
 * ## 键与命名空间
 * `ashare.assistant.sessions`（会话列表）/ `ashare.assistant.current`（当前会话 id），
 * 与既有 `ashare.assistant.pos`（悬浮球位置）、`ashare.notifications.*` 同前缀。
 *
 * ## 三条硬规矩
 * 1. **`streaming` 不可持久化**：正在生成的回答若原样存下，刷新后那条消息会
 *    **永远停在"思考中"**（没有 SSE 连接再回来推进它）。落盘/回读一律转
 *    `interrupted`（与界面上的"已停止生成"同义）；正文还是空的（一个字都没吐）
 *    整条丢弃 —— 恢复一条空回复没有意义。
 * 2. **条数与写失败都要有兜底**：localStorage 配额约 5MB 且**按 origin 计**，
 *    写不下时 `setItem` 抛的是**同步异常**。`saveSessions` 在写失败时
 *    **逐条淘汰最旧会话再重试**，直到写进去或列表清空 —— 宁可丢最旧的，
 *    也不能让"存不下"静默变成"整个功能失效"（`lib/notification-read.ts`
 *    对配额的态度同源：状态丢失不影响读取，但绝不把异常抛给调用方）。
 * 3. **解析必须容错**：库里的值可能是旧版本写的、被别的工具改过的、或被手工
 *    编辑坏的。逐条校验，**能救的都救**，不因一条脏数据丢掉整份历史。
 *
 * ## 边界（如实声明，刻意未实现）
 * **多标签页不做同步**：两个标签页各持一份列表副本、各自落盘，后写的覆盖先写的
 * （last-write-wins）。要做到真正一致得引入版本号 + 合并规则
 * （`notification-read` 的单调合并形态），而会话是**追加式长文本**、没有天然
 * 单调量（"谁更新"不等于"谁更全"），收益不抵复杂度。单标签页使用无影响。
 */

/** 消息（= 组件里的 `ChatMsg`；组件直接 `type ChatMsg = StoredMsg`，防两处漂移） */
export interface StoredMsg {
  id: number;
  role: "user" | "assistant";
  content: string;
  status: "ok" | "streaming" | "interrupted" | "error";
  /** 本条回答引用到的实时快照溯源 */
  sources?: { symbol: string; name: string; source: string; as_of: string }[];
  /** 本条回答实际调用过的工具（中文短标签） */
  tools?: string[];
}

export interface StoredSession {
  id: string;
  /** 首句用户提问（截断）——列表里显示的就是它 */
  title: string;
  /** 最后更新时刻（epoch ms），用于排序 */
  updatedAt: number;
  messages: StoredMsg[];
}

export const SESSIONS_KEY = "ashare.assistant.sessions";
export const CURRENT_KEY = "ashare.assistant.current";

/** 会话条数上界（需求：「存最近 10 条会话」） */
export const MAX_SESSIONS = 10;
/** 单会话消息条数上界：超出丢**最早**的（保留最近上下文，且给配额留出余量） */
export const MAX_MESSAGES = 100;
/** 标题长度上界（按字符计，中文一字即一位） */
export const TITLE_MAX = 24;

const ROLES = new Set(["user", "assistant"]);
const STATUSES = new Set(["ok", "streaming", "interrupted", "error"]);

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function storage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null; // 隐私模式 / 禁 cookie：降级为「无历史」，绝不抛
  }
}

/* ------------------------------------------------------------------ 归一化 */

/** 会话 id 去重：非法（非正整数）或与前面重复的 id 就地换成未占用的最小正整数。 */
function ensureUniqueIds(msgs: StoredMsg[]): StoredMsg[] {
  const used = new Set<number>();
  let next = 1;
  return msgs.map((m) => {
    let id = m.id;
    if (!Number.isInteger(id) || id <= 0 || used.has(id)) {
      while (used.has(next)) next += 1;
      id = next;
    }
    used.add(id);
    return id === m.id ? m : { ...m, id };
  });
}

function normalizeMsg(raw: unknown): StoredMsg | null {
  if (!isRecord(raw)) return null;
  const role = raw.role;
  if (typeof role !== "string" || !ROLES.has(role)) return null;
  const content = typeof raw.content === "string" ? raw.content : "";
  // 空正文没有恢复价值：用户消息必定非空（发送前 trim 过），所以这里挡的都是
  // "一个字都没吐出来"的助手消息 —— 恢复它只会得到一个空泡泡 + 永久活动提示。
  if (!content) return null;
  const rawStatus = typeof raw.status === "string" && STATUSES.has(raw.status) ? raw.status : "ok";
  const msg: StoredMsg = {
    id: typeof raw.id === "number" && Number.isFinite(raw.id) ? raw.id : 0,
    role: role as StoredMsg["role"],
    content,
    // 进程内的 "streaming" 落盘即失去意义，一律记作"已中断"
    status: rawStatus === "streaming" ? "interrupted" : (rawStatus as StoredMsg["status"]),
  };
  if (Array.isArray(raw.tools)) {
    const tools = raw.tools.filter((t): t is string => typeof t === "string" && !!t).slice(0, 20);
    if (tools.length) msg.tools = tools;
  }
  if (Array.isArray(raw.sources)) {
    const sources = raw.sources
      .filter(isRecord)
      .map((s) => ({
        symbol: String(s.symbol ?? ""),
        name: String(s.name ?? ""),
        source: String(s.source ?? ""),
        as_of: String(s.as_of ?? ""),
      }))
      .filter((s) => s.symbol);
    if (sources.length) msg.sources = sources;
  }
  return msg;
}

/**
 * 任意输入 → 合法消息数组（脏元素丢弃）。超出 `MAX_MESSAGES` 时**保留尾部**。
 *
 * 同时用作组件的「落盘前转换」：`toStoredMessages(messages)` 与回读走**同一套**
 * 校验，因此"能存进去的"与"能读出来的"是同一个集合 —— 不会出现存了却读不回的形状。
 */
export function toStoredMessages(raw: unknown): StoredMsg[] {
  if (!Array.isArray(raw)) return [];
  const out: StoredMsg[] = [];
  for (const item of raw) {
    const m = normalizeMsg(item);
    if (m) out.push(m);
  }
  const uniq = ensureUniqueIds(out);
  return uniq.length > MAX_MESSAGES ? uniq.slice(-MAX_MESSAGES) : uniq;
}

/** 标题 = 首句用户提问（空白折叠后截断）；没有用户消息时退回首条内容。 */
export function titleFromMessages(messages: readonly StoredMsg[]): string {
  const first =
    messages.find((m) => m.role === "user" && m.content.trim()) ?? messages.find((m) => m.content.trim());
  const text = (first?.content ?? "").replace(/\s+/g, " ").trim();
  if (!text) return "新会话";
  return text.length > TITLE_MAX ? `${text.slice(0, TITLE_MAX)}…` : text;
}

/** 新→旧排序；`updatedAt` 相同时按 id 兜底，保证顺序确定（测试依赖确定性）。 */
function sortSessions(list: StoredSession[]): StoredSession[] {
  return [...list].sort((a, b) => b.updatedAt - a.updatedAt || a.id.localeCompare(b.id));
}

function capSessions(list: StoredSession[]): StoredSession[] {
  return list.slice(0, MAX_SESSIONS);
}

/** 任意输入 → 合法会话列表（按 `updatedAt` 新→旧、截到 `MAX_SESSIONS`）。 */
export function normalizeSessions(raw: unknown): StoredSession[] {
  if (!Array.isArray(raw)) return [];
  const seen = new Set<string>();
  const out: StoredSession[] = [];
  for (const item of raw) {
    if (!isRecord(item)) continue;
    const id = typeof item.id === "string" ? item.id.trim() : "";
    if (!id || seen.has(id)) continue;
    const messages = toStoredMessages(item.messages);
    if (!messages.length) continue; // 空会话不入列表（不占额度、也没有恢复价值）
    seen.add(id);
    out.push({
      id,
      title:
        typeof item.title === "string" && item.title.trim()
          ? item.title.trim()
          : titleFromMessages(messages),
      updatedAt:
        typeof item.updatedAt === "number" && Number.isFinite(item.updatedAt) ? item.updatedAt : 0,
      messages,
    });
  }
  return capSessions(sortSessions(out));
}

/* ------------------------------------------------------------------ 读写 */

export function loadSessions(store: Storage | null = storage()): StoredSession[] {
  if (!store) return [];
  try {
    const raw = store.getItem(SESSIONS_KEY);
    if (!raw) return [];
    return normalizeSessions(JSON.parse(raw));
  } catch {
    // JSON 整体坏掉是不可恢复的（没有逐条解析的余地）⇒ 从空开始，不抛。
    // 单个会话/单条消息坏掉的情形由 normalizeSessions 逐条兜住（见上文规矩 3）。
    return [];
  }
}

export function saveSessions(list: readonly StoredSession[], store: Storage | null = storage()): void {
  if (!store) return;
  let pending = capSessions(sortSessions([...list]));
  for (;;) {
    try {
      store.setItem(SESSIONS_KEY, JSON.stringify(pending));
      return;
    } catch {
      // 配额超限 / 隐私模式：淘汰最旧的一条再试。pending 为空时必然退出，
      // 因此隐私模式（setItem 恒抛）不会死循环，只是"存不下"。
      if (!pending.length) return;
      pending = pending.slice(0, -1);
    }
  }
}

export function loadCurrentId(store: Storage | null = storage()): string | null {
  if (!store) return null;
  try {
    const raw = store.getItem(CURRENT_KEY);
    return raw && raw.trim() ? raw.trim() : null;
  } catch {
    return null;
  }
}

export function saveCurrentId(id: string, store: Storage | null = storage()): void {
  if (!store) return;
  try {
    store.setItem(CURRENT_KEY, id);
  } catch {
    /* 配额/隐私模式：记不住"当前是哪条"只影响下次刷新落在哪，不影响历史本身 */
  }
}

/* ------------------------------------------------------------------ 列表操作 */

let seq = 0;

/**
 * 新会话 id。用「时间戳 base36 + 进程内自增序号」，不依赖 `Math.random`
 * —— 同一次会话内绝无重复，且无需注入随机源即可确定性测试。
 */
export function newSessionId(now: number = Date.now()): string {
  seq += 1;
  return `s${now.toString(36)}-${seq}`;
}

/** 插入或替换（同 id 覆盖），随后重排 + 截断。 */
export function upsertSession(
  list: readonly StoredSession[],
  session: StoredSession,
): StoredSession[] {
  return capSessions(sortSessions([session, ...list.filter((s) => s.id !== session.id)]));
}

export function removeSession(list: readonly StoredSession[], id: string): StoredSession[] {
  return list.filter((s) => s.id !== id);
}
