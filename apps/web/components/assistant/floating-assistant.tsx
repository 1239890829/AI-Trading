"use client";

/**
 * 全局 AI 助手浮窗（所有模块页面可用）。
 *
 * - 悬浮球：pointer 拖动、松手吸附左右边缘、位置 localStorage 持久化；
 *   「移动 < 4px」判定为点击（展开/收起），避免拖动误触
 * - 聊天窗：SSE 流式渲染、中断（AbortController）/重新生成、最小化收回悬浮球
 * - 实体跳转：回答中的个股/题材经 entity-dict 词典识别 → 个股**就地弹窗**看详情、
 *   题材跳梯队页（2026-09-15 详情弹窗化，此前跳工作台）
 * - AI 判读提醒气泡：判读为 notify 的告警冒泡，点「查看详情」直达该股详情弹窗
 *   （2026-09-16，此前跳控制台告警页会丢当前页面上下文）
 * - 上下文：发送时带上当前页面 path/title/选中标的，后端注入系统提示
 * - 会话历史（IMP-004）：会话内容落 localStorage（最近 10 条）+ 历史列表，
 *   刷新不丢；存储契约与裁剪规则见 `lib/assistant-sessions.ts`
 *
 * 挂载在 app/layout.tsx（NavBar 之后），z-50 盖过导航（z-40）。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { API_BASE, type AgentBubble } from "@/lib/api";
import { themesUrl } from "@/lib/routing";
import { parseWorkbenchDetailUrl } from "@/lib/detail-tabs";
import { useSymbolDetail } from "@/components/detail/symbol-detail-context";
import { createEntityMatcher, type EntityDict, type EntityMatch } from "@/lib/entity-links";
import { isAllowedNav } from "@/lib/nav-targets";
import { RichText } from "@/components/assistant/rich-text";
import { AssistantMark } from "@/components/assistant/assistant-mark";
import { usePollingFetch } from "@/hooks/use-polling-fetch";
import {
  MAX_SESSIONS,
  loadCurrentId,
  loadSessions,
  newSessionId,
  removeSession as removeStoredSession,
  saveCurrentId,
  saveSessions,
  titleFromMessages,
  toStoredMessages,
  upsertSession,
  type StoredMsg,
  type StoredSession,
} from "@/lib/assistant-sessions";

const BALL = 48;
const MARGIN = 16;
const POS_KEY = "ashare.assistant.pos";
const PANEL_W = 400;
const PANEL_H = 560;

const PAGE_TITLES: Record<string, string> = {
  "/workbench": "工作台",
  "/tape": "盘面",
  "/market": "市场",
  "/hunting": "猎场",
  "/agent": "交易智能体",
};

const SUGGESTIONS = [
  "今天大盘和涨停池什么情况？",
  "帮我看看我的自选和持仓",
  "每日精选的选股逻辑是什么？",
];

/**
 * 消息类型 = 存储契约（`lib/assistant-sessions.ts` 的 `StoredMsg`）。
 *
 * 刻意**共用同一个类型**而不是各写一份：会话要落盘，两边字段一旦漂移，
 * 症状是"存进去读不回来"或"恢复了却少一块溯源"，而它只在刷新后才显形。
 */
type ChatMsg = StoredMsg;

/** 会话内容是否与已存的一致 —— 切会话/重渲染不该无谓刷新 `updatedAt` 与写盘。 */
function sameStoredMessages(a: readonly StoredMsg[], b: readonly StoredMsg[]): boolean {
  return a.length === b.length && JSON.stringify(a) === JSON.stringify(b);
}

/** 会话时间：今天只给 `HH:MM`，更早给 `MM-DD HH:MM`（浮窗里不写年份，省地方）。 */
function formatSessionTime(ms: number): string {
  const d = new Date(ms);
  if (Number.isNaN(d.getTime())) return "";
  const p = (n: number) => String(n).padStart(2, "0");
  const now = new Date();
  const sameDay =
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate();
  const hm = `${p(d.getHours())}:${p(d.getMinutes())}`;
  return sameDay ? hm : `${p(d.getMonth() + 1)}-${p(d.getDate())} ${hm}`;
}

/** 生成期进度（后端 status 事件）：thinking = 组织回答；tools = 正在取数 */
interface Activity {
  phase: "thinking" | "tools";
  label?: string;
}

/**
 * 生成中提示（2026-09-11 用户反馈「没有思考中/生成中的提示，一直在等待，以为不动了」）。
 *
 * 为什么必须显式做：一次取数 + 两轮生成在网关侧可达十几秒**零 delta**，
 * 此前只有"内容为空时一个 2px 呼吸光标"——用户看不到任何"在动"的信号。
 * 现在按后端 status 事件显示「思考中… / 正在取数：龙虎榜…」，三点错峰呼吸。
 */
function ActivityLine({ activity }: { activity: Activity | null }) {
  const label =
    activity?.phase === "tools"
      ? activity.label
        ? `正在取数：${activity.label}`
        : "正在取数"
      : "思考中";
  return (
    <span
      data-testid="assistant-activity"
      aria-live="polite"
      className="inline-flex items-center gap-1.5 text-[12px] text-zinc-600 dark:text-zinc-400"
    >
      <span className="flex items-center gap-[3px]" aria-hidden>
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            className="h-[3px] w-[3px] rounded-full bg-current motion-safe:animate-pulse"
            style={{ animationDelay: `${i * 160}ms` }}
          />
        ))}
      </span>
      {label}
    </span>
  );
}

function clampPos(p: { x: number; y: number }): { x: number; y: number } {
  const w = typeof window === "undefined" ? 1280 : window.innerWidth;
  const h = typeof window === "undefined" ? 800 : window.innerHeight;
  return {
    x: Math.min(Math.max(p.x, MARGIN), Math.max(MARGIN, w - BALL - MARGIN)),
    y: Math.min(Math.max(p.y, MARGIN), Math.max(MARGIN, h - BALL - MARGIN)),
  };
}

function snapEdge(p: { x: number; y: number }): { x: number; y: number } {
  const w = window.innerWidth;
  const h = window.innerHeight;
  const left = p.x + BALL / 2 < w / 2;
  return clampPos({ x: left ? MARGIN : w - BALL - MARGIN, y: p.y });
}

export function FloatingAssistant() {
  const router = useRouter();
  // 助手回复里的个股实体 → 就地弹窗看详情（2026-09-15 详情弹窗化，原先跳工作台）
  const { open: openSymbolDetail } = useSymbolDetail();
  const [mounted, setMounted] = useState(false);
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ x: number; y: number } | null>(null);
  const [viewport, setViewport] = useState({ w: 1280, h: 800 });
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [activity, setActivity] = useState<Activity | null>(null);
  const [model, setModel] = useState("");
  const [dict, setDict] = useState<EntityDict | null>(null);
  const [bubbles, setBubbles] = useState<AgentBubble[]>([]);
  const [docked, setDocked] = useState<"left" | "right" | null>(null);
  const [orbHovered, setOrbHovered] = useState(false);
  const [sessions, setSessions] = useState<StoredSession[]>([]);
  const [currentId, setCurrentId] = useState<string | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);

  const abortRef = useRef<AbortController | null>(null);
  const idRef = useRef(0);
  const msgsRef = useRef<ChatMsg[]>([]);
  const dictTriedRef = useRef(false);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const stickRef = useRef(true);
  /** 会话列表的权威副本：落盘/切换都读它，避免闭包里拿到过期数组。 */
  const sessionsRef = useRef<StoredSession[]>([]);
  /** 首帧恢复是否已完成 —— 未完成前不许落盘（否则会拿空列表覆盖历史）。 */
  const loadedRef = useRef(false);
  /**
   * 流式代际号。切换/新建/删除会话会 `+1` 弃掉在途的流：
   * 被弃的流**不得再写消息表、也不得再关掉界面上的"生成中"**
   * （否则它的中断兜底会把**新会话**的最后一条标成"已停止"，或把新流的
   * 状态栏关掉 —— 都是在用户看来毫无因果的错乱）。
   */
  const streamGenRef = useRef(0);

  const nextId = () => ++idRef.current;
  const syncRef = (updater: (prev: ChatMsg[]) => ChatMsg[]) => {
    msgsRef.current = updater(msgsRef.current);
    return msgsRef.current;
  };
  /** 会话列表的**唯一写入口**：ref 与 state 必须同步，否则下一次读到的还是旧数组。 */
  const commitSessions = useCallback((next: StoredSession[]) => {
    sessionsRef.current = next;
    setSessions(next);
  }, []);

  // ---- 初始化：位置恢复 + 视口跟踪 ----------------------------------------
  useEffect(() => {
    setMounted(true);
    setViewport({ w: window.innerWidth, h: window.innerHeight });
    try {
      const saved = localStorage.getItem(POS_KEY);
      if (saved) {
        const p = JSON.parse(saved);
        if (typeof p?.x === "number" && typeof p?.y === "number") setPos(clampPos(p));
      }
    } catch {}
    setPos((prev) => prev ?? {
      x: window.innerWidth - BALL - MARGIN,
      y: window.innerHeight - BALL - MARGIN - 64,
    });
    // 会话恢复（IMP-004）：先取列表，再按"记住的 id → 最新的那条 → 全新会话"三级兜底。
    // 三级都要有：记住的 id 可能已被淘汰/删除（列表上界 10 条），此时落回最新的一条，
    // 而不是让用户看到一片空白。
    try {
      const list = loadSessions();
      commitSessions(list);
      const want = loadCurrentId();
      const cur = (want ? list.find((s) => s.id === want) : undefined) ?? list[0];
      if (cur) {
        setCurrentId(cur.id);
        // ⚠️ `msgsRef` 才是后续所有变更的底稿（见 syncRef）：只 setMessages 不写它，
        // 恢复出来的历史会在**第一次发问时被整段覆盖掉**（提问看起来像把历史删了，
        // 而刷新一下又都回来了 —— 2026-09-15 组件测试抓到的形态）。
        msgsRef.current = cur.messages;
        setMessages(cur.messages);
        // id 计数器必须越过已恢复的最大 id，否则新消息会与旧消息**撞 key**
        idRef.current = cur.messages.reduce((mx, m) => Math.max(mx, m.id), 0);
      } else {
        setCurrentId(newSessionId());
      }
    } catch {
      setCurrentId(newSessionId());
    } finally {
      loadedRef.current = true;
    }
    const onResize = () => {
      setViewport({ w: window.innerWidth, h: window.innerHeight });
      setPos((p) => (p ? clampPos(p) : p));
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [commitSessions]);

  // ---- 会话落盘（IMP-004）--------------------------------------------------
  // 触发条件：消息变了 **且不在流式中**。流式期间每个 delta 都会改 messages，
  // 逐个 delta 序列化全量历史会让长对话明显卡顿；收流后落一次即可 ——
  // 中途刷新最多丢"正在生成的这一条"，而它本来也无法恢复成生成中
  // （见 `assistant-sessions.ts` 规矩 1：streaming 落盘即转 interrupted）。
  useEffect(() => {
    if (!loadedRef.current || !currentId || streaming) return;
    const stored = toStoredMessages(messages);
    if (!stored.length) return; // 空会话不入列表：新建会话不会凭空多出空记录
    const prev = sessionsRef.current.find((s) => s.id === currentId);
    if (prev && sameStoredMessages(prev.messages, stored)) return; // 内容没变就不写
    const next = upsertSession(sessionsRef.current, {
      id: currentId,
      title: titleFromMessages(stored),
      updatedAt: Date.now(),
      messages: stored,
    });
    commitSessions(next);
    saveSessions(next);
    saveCurrentId(currentId);
  }, [messages, streaming, currentId, commitSessions]);

  // ---- AI 判读提醒（悬浮球气泡）------------------------------------------
  // 只有判读为 notify 且未确认的才出现；规则触发本身不冒泡（防刷屏）。
  // 30s 轮询：告警不是秒级决策，且 triage worker 本身也是 30s 一轮。
  // 2026-09-11（S2-5）：裸 setInterval → 统一入口（获得可见性暂停）。
  // `marketHours: false` —— 判读提醒盘后同样需要及时冒泡，不套行情类降频。
  usePollingFetch(
    async () => {
      try {
        const { getAgentBubbles } = await import("@/lib/api");
        const list = await getAgentBubbles(5);
        setBubbles(list);
      } catch {
        setBubbles([]); // 后端未起/接口异常：静默，不打扰
      }
    },
    30_000,
    undefined,
    { enabled: mounted, marketHours: false }
  );

  async function ackBubble(id: number) {
    const { ackAgentTriage } = await import("@/lib/api");
    try {
      await ackAgentTriage(id);
      setBubbles((prev) => prev.filter((b) => b.id !== id));
    } catch {
      /* 确认失败只保留气泡，不弹错 */
    }
  }

  // ---- 实体字典：窗口首开时拉一次，失败静默（识别是增强层） ----------------
  useEffect(() => {
    if (!open || dictTriedRef.current) return;
    dictTriedRef.current = true;
    fetch(`${API_BASE}/api/assistant/entity-dict`)
      .then((r) => (r.ok ? r.json() : null))
      .then((j) => {
        if (j?.data) setDict(j.data as EntityDict);
        else dictTriedRef.current = false;
      })
      .catch(() => {
        dictTriedRef.current = false;
      });
  }, [open]);

  const matcher = useMemo(() => createEntityMatcher(dict), [dict]);

  // ---- 贴边收纳（2026-09-08 用户需求 10）：吸附到边缘后收进一半，hover 滑出 ----
  useEffect(() => {
    if (!pos || open) {
      setDocked(null);
      return;
    }
    const w = window.innerWidth;
    if (pos.x <= MARGIN + 2) setDocked("left");
    else if (pos.x >= w - BALL - MARGIN - 2) setDocked("right");
    else setDocked(null);
  }, [pos, open]);

  // ---- 拖动 + 吸附 ---------------------------------------------------------
  const drag = useRef<{ px: number; py: number; ox: number; oy: number; moved: boolean } | null>(null);

  const onBallPointerDown = (e: React.PointerEvent) => {
    if (!pos) return;
    e.preventDefault();
    // pointer capture 失败（如合成事件无真实指针 id）不应中断拖动流程
    try {
      (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    } catch {}
    drag.current = { px: e.clientX, py: e.clientY, ox: pos.x, oy: pos.y, moved: false };
  };
  const onBallPointerMove = (e: React.PointerEvent) => {
    const d = drag.current;
    if (!d) return;
    const dx = e.clientX - d.px;
    const dy = e.clientY - d.py;
    if (Math.abs(dx) > 4 || Math.abs(dy) > 4) d.moved = true;
    setPos(clampPos({ x: d.ox + dx, y: d.oy + dy }));
  };
  const onBallPointerUp = () => {
    const d = drag.current;
    drag.current = null;
    if (!d) return;
    if (!d.moved) {
      setOpen((o) => !o);
      return;
    }
    setPos((p) => {
      const s = p ? snapEdge(p) : p;
      if (s) {
        try {
          localStorage.setItem(POS_KEY, JSON.stringify(s));
        } catch {}
      }
      return s;
    });
  };

  // ---- 流式请求 ------------------------------------------------------------
  const patchLast = useCallback((patch: Partial<ChatMsg>) => {
    setMessages(syncRef((prev) => {
      if (!prev.length) return prev;
      const next = [...prev];
      next[next.length - 1] = { ...next[next.length - 1], ...patch };
      return next;
    }));
  }, []);

  const runStream = useCallback(async (history: ChatMsg[]) => {
    const gen = ++streamGenRef.current;
    const assistantId = nextId();
    setMessages(syncRef((prev) => [
      ...prev,
      { id: assistantId, role: "assistant", content: "", status: "streaming" },
    ]));
    setStreaming(true);
    setActivity({ phase: "thinking" });
    stickRef.current = true;
    const ac = new AbortController();
    abortRef.current = ac;
    // 页面上下文：发送瞬间取（不依赖 hook，避免 useSearchParams 的 SSR 限制）
    const path = typeof window !== "undefined" ? window.location.pathname + window.location.search : "";
    const page: Record<string, string> = { path };
    if (typeof document !== "undefined") {
      const hit = Object.entries(PAGE_TITLES).find(([p]) => path.startsWith(p));
      if (hit) page.title = hit[1];
      if (path.startsWith("/workbench")) {
        const sym = new URLSearchParams(window.location.search).get("symbol");
        if (sym) page.symbol = sym;
      }
    }
    try {
      const res = await fetch(`${API_BASE}/api/assistant/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          messages: history.map((m) => ({ role: m.role, content: m.content })),
          page,
        }),
        signal: ac.signal,
      });
      if (!res.ok || !res.body) {
        let detail = `HTTP ${res.status}`;
        try {
          const j = await res.json();
          if (j?.detail) detail = String(j.detail);
        } catch {}
        throw new Error(detail);
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      for (;;) {
        // 会话已被切走/新建/删除 ⇒ 这条流的一切产出都作废（见 streamGenRef 注释）
        if (gen !== streamGenRef.current) break;
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let idx: number;
        while ((idx = buf.indexOf("\n\n")) >= 0) {
          // 再查一次：能插进来的只有 `await reader.read()` 那一处 await，
          // 因此"切会话"必然发生在读到数据与处理数据之间 —— 少了这道，
          // 上面那道守卫会晚一拍，缓冲里的 delta 仍会写进**新会话**。
          if (gen !== streamGenRef.current) break;
          const raw = buf.slice(0, idx);
          buf = buf.slice(idx + 2);
          for (const line of raw.split("\n")) {
            if (!line.startsWith("data: ")) continue;
            let ev: Record<string, unknown>;
            try {
              ev = JSON.parse(line.slice(6));
            } catch {
              continue;
            }
            if (ev.type === "meta") {
              if (typeof ev.model === "string") setModel(ev.model);
              // 溯源清单挂到最后一条（正在生成的 assistant 消息）上
              const srcs = Array.isArray(ev.sources) ? ev.sources : [];
              if (srcs.length) {
                setMessages(syncRef((prev) => {
                  if (!prev.length) return prev;
                  const next = [...prev];
                  const lastMsg = next[next.length - 1];
                  next[next.length - 1] = {
                    ...lastMsg,
                    sources: srcs as ChatMsg["sources"],
                  };
                  return next;
                }));
              }
            } else if (ev.type === "status") {
              // 进度提示：取数/思考期间可能十几秒没有任何 delta，必须靠它给反馈
              const phase = ev.phase === "tools" ? "tools" : "thinking";
              setActivity({
                phase,
                label: typeof ev.label === "string" ? ev.label : undefined,
              });
            } else if (ev.type === "tools" && Array.isArray(ev.used)) {
              // 工具回执：让用户知道这条答案取过数，不是模型凭空编的。
              // 优先用后端给的中文标签（labels），旧事件没带就退回键名。
              const labels = Array.isArray(ev.labels)
                ? (ev.labels as unknown[]).map(String)
                : (ev.used as unknown[]).map(String);
              setMessages(syncRef((prev) => {
                if (!prev.length) return prev;
                const next = [...prev];
                next[next.length - 1] = { ...next[next.length - 1], tools: labels };
                return next;
              }));
            } else if (ev.type === "delta" && typeof ev.text === "string") {
              // 正文开始回流 → 退出"取数中"；已经是 thinking 时保持原引用，避免每个 delta 多一次渲染
              setActivity((a) => (a?.phase === "tools" ? { phase: "thinking" } : a));
              setMessages(syncRef((prev) => {
                if (!prev.length) return prev;
                const next = [...prev];
                const lastMsg = next[next.length - 1];
                next[next.length - 1] = { ...lastMsg, content: lastMsg.content + (ev.text as string) };
                return next;
              }));
            } else if (ev.type === "error" && typeof ev.message === "string") {
              // 后端带 kind/hint：优先显示可行动提示（如"额度不足，需充值"），
              // 技术原文降为次要行——过去只回英文报错，用户不知道该做什么。
              const hint = typeof ev.hint === "string" ? ev.hint : "";
              patchLast({
                status: "error",
                content: hint ? `⚠️ ${hint}\n\n${ev.message}` : `⚠️ ${ev.message}`,
              });
            } else if (ev.type === "done") {
              patchLast({ status: "ok" });
            }
          }
        }
      }
    } catch (err) {
      // 已被弃掉的流不写消息表：否则它会把**新会话**的最后一条标成"已停止"
      if (gen !== streamGenRef.current) return;
      const aborted = err instanceof DOMException && err.name === "AbortError";
      setMessages(syncRef((prev) => {
        if (!prev.length) return prev;
        const next = [...prev];
        const lastMsg = next[next.length - 1];
        if (aborted) {
          next[next.length - 1] = {
            ...lastMsg,
            status: "interrupted",
            content: lastMsg.content || "（已停止）",
          };
        } else {
          const detail = err instanceof Error ? err.message : String(err);
          next[next.length - 1] = {
            ...lastMsg,
            status: "error",
            content: lastMsg.content || `⚠️ 请求失败：${detail}`,
          };
        }
        return next;
      }));
    } finally {
      // 被弃掉的流不得再动界面状态（否则会把新流的"生成中"关掉）
      if (gen === streamGenRef.current) {
        abortRef.current = null;
        setStreaming(false);
        setActivity(null);
      }
    }
  }, [patchLast]);

  const send = (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || streaming) return;
    const userMsg: ChatMsg = { id: nextId(), role: "user", content: trimmed, status: "ok" };
    setMessages(syncRef((prev) => [...prev, userMsg]));
    setInput("");
    void runStream(syncRef((prev) => prev));
  };

  const stop = () => {
    abortRef.current?.abort();
    setActivity(null);
    // 兜底：不等 abort 让 read() 拒绝，直接落中断状态（与 catch 路径幂等）
    setMessages(syncRef((prev) => {
      if (!prev.length || prev[prev.length - 1].status !== "streaming") return prev;
      const next = [...prev];
      const lastMsg = next[next.length - 1];
      next[next.length - 1] = {
        ...lastMsg,
        status: "interrupted",
        content: lastMsg.content || "（已停止）",
      };
      return next;
    }));
    setStreaming(false);
  };

  const regenerate = () => {
    if (streaming) return;
    const prev = msgsRef.current;
    // 去掉末尾 assistant 回复，以「…以 user 结尾」的历史重发
    let end = prev.length;
    while (end > 0 && prev[end - 1].role !== "user") end -= 1;
    if (end === 0) return;
    const history = prev.slice(0, end);
    setMessages(syncRef(() => [...history]));
    void runStream(history);
  };

  /**
   * 弃掉在途的流（切会话 / 新建 / 删除前的统一动作）。
   * **顺序不能反**：必须先 `+1` 让在途流失效，再 abort —— 否则它的中断兜底会
   * 先一步把消息写进"新会话"。
   */
  const discardStream = () => {
    if (!streaming) return;
    streamGenRef.current += 1;
    abortRef.current?.abort();
    setStreaming(false);
    setActivity(null);
  };

  /** 新建会话：当前会话在收流时已落盘，这里只切到一个空会话（空会话不入历史）。 */
  const startNewSession = () => {
    discardStream();
    setMessages(syncRef(() => []));
    const id = newSessionId();
    setCurrentId(id);
    saveCurrentId(id);
    setHistoryOpen(false);
  };

  const switchSession = (id: string) => {
    setHistoryOpen(false);
    if (id === currentId) return;
    const target = sessionsRef.current.find((s) => s.id === id);
    if (!target) return;
    discardStream();
    const restored = target.messages.map((m) => ({ ...m }));
    setMessages(syncRef(() => restored));
    // 计数器只增不减：跨会话也绝不撞 key
    idRef.current = restored.reduce((mx, m) => Math.max(mx, m.id), idRef.current);
    setCurrentId(id);
    saveCurrentId(id);
    stickRef.current = true;
  };

  const dropSession = (id: string) => {
    const next = removeStoredSession(sessionsRef.current, id);
    commitSessions(next);
    saveSessions(next);
    // 删空了就得收起列表：历史入口只在"有会话"时渲染，留着打开的空列表会无处可退
    if (!next.length) setHistoryOpen(false);
    if (id !== currentId) return;
    // 删的正是当前会话 ⇒ 原地换成全新会话，不留下"当前 id 指向已删记录"的状态
    discardStream();
    setMessages(syncRef(() => []));
    const nid = newSessionId();
    setCurrentId(nid);
    saveCurrentId(nid);
  };

  // ---- 跳转 ----------------------------------------------------------------
  // 四类落点：个股 → **就地弹窗**（命中页签则直达该页签）；题材 → 盘面题材梯队；
  // 功能入口 → 注册表给出的站内深链。
  // nav 与「个股 + 页签」的 URL 在识别阶段已过白名单守卫，这里再过一次
  // （防御渲染期被篡改），未过则按类型降级，绝不 push 非常规 URL。
  //
  // P1-2（2026-09-11）：必须 useCallback —— 它作为 `onNavigate` 传给 RichText，
  // 而 RichText 已包 memo；每次渲染新建函数会让 memo 彻底失效（流式期间
  // 每个 delta 仍重解析全部历史消息，等于白做）。
  const onNavigate = useCallback(
    (m: EntityMatch) => {
      if (m.url && isAllowedNav(m.url)) {
        // 深链优先。2026-09-15 详情弹窗化：个股深链（`/workbench?symbol=…&ct=…&rt=…`）
        // 经解析还原为弹窗入参，**页签意图一并带走**（"看看 600519 的资金流向图"
        // 直接落在资金图页签）；其余站内功能入口深链照常跳转。
        const target = parseWorkbenchDetailUrl(m.url);
        if (target) {
          openSymbolDetail(target);
          setOpen(false);
          return;
        }
        router.push(m.url);
      } else if (m.type === "nav") {
        // nav 必有 url，走到这里即守卫未过（防御性降级）
        router.push(themesUrl(m.name));
      } else if (m.type === "stock" && m.code) {
        openSymbolDetail({ symbol: m.code });
      } else {
        router.push(themesUrl(m.name));
      }
      setOpen(false);
    },
    [router, openSymbolDetail],
  );

  // ---- 自动滚动（用户上翻即停止跟随） --------------------------------------
  useEffect(() => {
    const el = scrollRef.current;
    if (el && stickRef.current) el.scrollTop = el.scrollHeight;
  }, [messages]);

  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  };

  // ---- 渲染 ----------------------------------------------------------------
  if (!mounted || !pos) return null;

  const side: "left" | "right" = pos.x + BALL / 2 < viewport.w / 2 ? "left" : "right";
  const panelW = Math.min(PANEL_W, viewport.w - MARGIN * 2);
  const panelH = Math.min(PANEL_H, viewport.h - MARGIN * 2);
  const panelBottom = Math.min(
    Math.max(viewport.h - (pos.y + BALL + 12), MARGIN),
    Math.max(MARGIN, viewport.h - panelH - MARGIN),
  );
  const panelStyle: React.CSSProperties = {
    width: panelW,
    height: panelH,
    bottom: panelBottom,
    ...(side === "left" ? { left: MARGIN } : { right: MARGIN }),
  };

  const lastAssistantIdx = messages.length - 1;

  return (
    <>
      {/* 悬浮球：墨玉反色（light 深墨 / dark 亮面）在任何页面上都可读；
          rose 细环是唯一的品牌色——hairline 级克制，不做渐变球。
          贴边收纳（2026-09-09 用户要求重设计）：不再是被截断的球，morph 成
          墨玉半胶囊把手贴在屏幕边缘（rose 细竖条提示），hover 平滑展开为完整球
          亮色档 2026-09-11 修正（P2-27）：原 `text-zinc-600` 在 `bg-zinc-900` 上仅 **2.31:1**，
          低于非文本图形 3:1。**注意坏的不是颜色而是配对**——`text-zinc-600` 在亮面上完全合规，
          是「墨玉底」让它失效的。取 zinc-400（6.91:1）而非 zinc-100：球已有 shadow + rose 环
          做边界，mark 保持克制的弱化观感；深色侧 `dark:bg-zinc-100 dark:text-zinc-950`（16.12:1）
          原本就达标，未动。 */}
      <div
        data-testid="assistant-ball"
        role="button"
        aria-label="AI 助手"
        className={`fixed z-50 flex cursor-grab select-none items-center justify-center bg-zinc-900 text-zinc-400 shadow-[0_2px_8px_rgba(0,0,0,0.18),0_10px_28px_rgba(0,0,0,0.22)] transition-[left,width,height,border-radius,box-shadow] duration-200 ease-out hover:shadow-[0_4px_12px_rgba(0,0,0,0.22),0_14px_36px_rgba(0,0,0,0.28)] active:cursor-grabbing dark:bg-zinc-100 dark:text-zinc-950 dark:shadow-[0_2px_8px_rgba(0,0,0,0.4),0_10px_28px_rgba(0,0,0,0.35)] ${
          docked && !orbHovered
            ? "ring-0"
            : "ring-1 ring-rose-500/45 dark:ring-rose-500/55"
        }`}
        style={
          docked && !orbHovered
            ? {
                // 把手形态：紧贴边缘的半胶囊（22×44），rose 细竖条做呼吸提示
                left: docked === "left" ? 0 : viewport.w - 22,
                top: pos.y + (BALL - 44) / 2,
                width: 22, height: 44,
                borderRadius: docked === "left" ? "0 22px 22px 0" : "22px 0 0 22px",
              }
            : {
                left: pos.x, top: pos.y, width: BALL, height: BALL,
                borderRadius: "50%",
              }
        }
        onPointerEnter={() => setOrbHovered(true)}
        onPointerLeave={() => setOrbHovered(false)}
        onPointerDown={onBallPointerDown}
        onPointerMove={onBallPointerMove}
        onPointerUp={onBallPointerUp}
      >
        {docked && !orbHovered ? (
          // 把手态：rose 细竖条（hairline 品牌色），有告警时红点计数替代
          bubbles.length > 0 ? (
            <span className="flex h-4 min-w-3 items-center justify-center rounded-full bg-rose-500 px-0.5 text-[9px] font-semibold text-white">
              {bubbles.length}
            </span>
          ) : (
            <span className="h-4 w-[3px] rounded-full bg-rose-400/80" aria-hidden />
          )
        ) : (
          <>
            <AssistantMark size={22} />
            {streaming && !open && (
              <span className="absolute -right-0.5 -top-0.5 h-3 w-3 animate-pulse rounded-full border-2 border-zinc-900 bg-emerald-400 dark:border-zinc-100" />
            )}
            {/* 告警红点：只统计 AI 判为"值得提醒"的（notify 且未确认） */}
            {bubbles.length > 0 && !open && (
              <span
                data-testid="assistant-alert-dot"
                className="absolute -right-1 -top-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-rose-500 px-1 text-[10px] font-semibold text-white ring-2 ring-white dark:ring-zinc-900"
              >
                {bubbles.length}
              </span>
            )}
          </>
        )}
      </div>

      {/* 提醒气泡：AI 判读后才出现（规则触发 ≠ 值得提醒）；
          点「查看详情」**就地打开该股详情弹窗**（2026-09-16），多条时另有入口进告警页 */}
      {bubbles.length > 0 && !open && (
        <div
          data-testid="assistant-alert-bubble"
          className="fixed z-50 w-[280px] rounded-xl border border-zinc-200 bg-white/95 p-3 shadow-xl backdrop-blur dark:border-zinc-800 dark:bg-zinc-950/95"
          style={{
            left: pos.x - 292 > 8 ? pos.x - 292 : pos.x + BALL + 12,
            top: Math.max(8, pos.y - 8),
          }}
        >
          <div className="mb-1 flex items-center justify-between gap-2">
            <span className="text-[11px] font-medium text-zinc-700 dark:text-zinc-200">
              AI 判读提醒 · {bubbles.length} 条
            </span>
            {bubbles[0].model === "llm_fallback" && (
              <span className="rounded bg-amber-500/10 px-1 py-0.5 text-[10px] text-amber-800 dark:text-amber-300">
                按规则提醒
              </span>
            )}
          </div>
          <p className="line-clamp-2 text-[11px] leading-relaxed text-zinc-600 dark:text-zinc-300">
            {bubbles[0].symbol ? `${bubbles[0].symbol} · ` : ""}
            {bubbles[0].name ? `${bubbles[0].name} · ` : ""}
            {bubbles[0].reason || "触发告警"}
          </p>
          <div className="mt-2 flex items-center gap-1.5">
            <button
              type="button"
              data-testid="assistant-alert-view"
              onClick={() => {
                // 2026-09-16 用户指令：个股提醒点开**直接看这只股的详情弹窗**，不再跳告警页
                // ——原实现 `router.push("/agent?tab=alerts")` 会把用户从当前页面连根拔走，
                // 而气泡里的正文已经说明是哪只股，用户想看的就是那只股本身。
                // 兜底：无代码的判读（后端 `pending_bubbles` 已按「缺 symbol+name 视为无效」过滤，
                // 此处是防御性分支）才回退告警页，不静默失败。
                const b = bubbles[0];
                if (b.symbol) openSymbolDetail({ symbol: b.symbol });
                else router.push("/agent?tab=alerts");
              }}
              className="rounded-md border border-zinc-300 px-2 py-0.5 text-[11px] text-zinc-700 hover:bg-zinc-100 dark:border-zinc-600 dark:text-zinc-200 dark:hover:bg-zinc-800"
            >
              查看详情
            </button>
            {/* 气泡只展示第一条；多条时保留通往告警页（全量 + 判读记录）的入口，
                否则第 2 条起再无入口 —— 收敛主按钮不能以丢失能力为代价 */}
            {bubbles.length > 1 && (
              <button
                type="button"
                data-testid="assistant-alert-all"
                onClick={() => router.push("/agent?tab=alerts")}
                className="rounded-md px-2 py-0.5 text-[11px] text-zinc-600 hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-800"
              >
                全部 {bubbles.length} 条
              </button>
            )}
            <button
              type="button"
              onClick={() => void ackBubble(bubbles[0].id)}
              className="rounded-md px-2 py-0.5 text-[11px] text-zinc-600 hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-800"
            >
              忽略
            </button>
          </div>
        </div>
      )}

      {/* 聊天窗 */}
      {open && (
        <div
          data-testid="assistant-panel"
          className="fixed z-50 flex flex-col overflow-hidden rounded-2xl border border-zinc-200 bg-white/95 shadow-2xl shadow-zinc-900/10 backdrop-blur dark:border-zinc-800 dark:bg-zinc-950/95 dark:shadow-black/50"
          style={panelStyle}
        >
          {/* 头部：墨玉徽标 + 名称 + 模型（mono 弱化），按钮族统一次要级 */}
          <div className="flex items-center gap-2.5 border-b border-zinc-200/80 px-4 py-3 dark:border-zinc-800/80">
            {/* 与悬浮球同款墨玉反色，档位同步（P2-27） */}
            <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-zinc-900 text-zinc-400 dark:bg-zinc-100 dark:text-zinc-950">
              <AssistantMark size={15} strokeWidth={2} />
            </span>
            <div className="min-w-0 flex-1">
              <div className="text-[13px] font-semibold tracking-tight text-zinc-900 dark:text-zinc-100">AI 助手</div>
              {model && (
                <div className="truncate font-mono text-[10px] leading-tight text-zinc-600 dark:text-zinc-400">{model}</div>
              )}
            </div>
            {messages.length > 0 && (
              <button
                type="button"
                aria-label="新建会话"
                title="新建会话"
                onClick={startNewSession}
                className="rounded-md p-1.5 text-zinc-600 dark:text-zinc-400 hover:bg-zinc-100 hover:text-zinc-600 dark:hover:bg-zinc-800 dark:hover:text-zinc-300"
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                  <path d="M12 5v14M5 12h14" />
                </svg>
              </button>
            )}
            {sessions.length > 0 && (
              <button
                type="button"
                data-testid="assistant-history-toggle"
                aria-label="历史会话"
                aria-expanded={historyOpen}
                title="历史会话"
                onClick={() => setHistoryOpen((v) => !v)}
                className={`rounded-md p-1.5 hover:bg-zinc-100 dark:hover:bg-zinc-800 ${
                  historyOpen
                    ? "text-rose-600 dark:text-rose-400"
                    : "text-zinc-600 dark:text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-300"
                }`}
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                  <path d="M12 7v5l3 2" />
                  <path d="M3.05 11a9 9 0 1 1 .5 4" />
                  <path d="M3 4v5h5" />
                </svg>
              </button>
            )}
            <button
              type="button"
              aria-label="最小化"
              title="最小化"
              onClick={() => setOpen(false)}
              className="rounded-md p-1.5 text-zinc-600 dark:text-zinc-400 hover:bg-zinc-100 hover:text-zinc-600 dark:hover:bg-zinc-800 dark:hover:text-zinc-300"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden>
                <path d="M5 12h14" />
              </svg>
            </button>
          </div>

          {/* 会话历史列表（IMP-004）：**替换**消息区而不是另开一页 ——
              400px 浮窗里分页会割裂上下文，替换式列表能保留头部与输入区不动。
              列表本身不再另加弹层，省掉一套定位/边界计算。 */}
          {historyOpen ? (
            <div
              data-testid="assistant-history"
              className="min-h-0 flex-1 overflow-y-auto px-2 py-2"
            >
              <div className="flex items-center justify-between px-2 pb-1.5 text-[11px] text-zinc-500 dark:text-zinc-400">
                <span>历史会话</span>
                <span data-testid="assistant-history-count">
                  {sessions.length} / {MAX_SESSIONS}
                </span>
              </div>
              <ul className="space-y-1">
                {sessions.map((s) => (
                  <li key={s.id} className="flex items-center gap-1">
                    <button
                      type="button"
                      data-testid="assistant-history-item"
                      data-session-id={s.id}
                      aria-current={s.id === currentId ? "true" : undefined}
                      onClick={() => switchSession(s.id)}
                      className={`min-w-0 flex-1 rounded-lg px-2.5 py-2 text-left transition-colors hover:bg-zinc-100 dark:hover:bg-zinc-800 ${
                        s.id === currentId ? "bg-rose-50 dark:bg-rose-500/10" : ""
                      }`}
                    >
                      <div className="truncate text-[12px] text-zinc-800 dark:text-zinc-200">
                        {s.title}
                      </div>
                      <div className="mt-0.5 flex items-center gap-1.5 text-[10px] text-zinc-500 dark:text-zinc-400">
                        {s.id === currentId && (
                          <span className="rounded bg-rose-600 px-1 py-px text-[9px] text-white">
                            当前
                          </span>
                        )}
                        <span>
                          {s.messages.length} 条 · {formatSessionTime(s.updatedAt)}
                        </span>
                      </div>
                    </button>
                    <button
                      type="button"
                      aria-label={`删除会话：${s.title}`}
                      title="删除会话"
                      onClick={() => dropSession(s.id)}
                      className="shrink-0 rounded-md p-1 text-zinc-500 hover:bg-zinc-100 hover:text-zinc-700 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"
                    >
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden>
                        <path d="M6 6l12 12M18 6 6 18" />
                      </svg>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          ) : (
          <div
            ref={scrollRef}
            onScroll={onScroll}
            className="min-h-0 flex-1 space-y-4 overflow-y-auto px-4 py-3.5"
          >
            {messages.length === 0 && (
              <div className="flex h-full flex-col justify-center gap-2.5">
                <div className="text-sm font-medium text-zinc-700 dark:text-zinc-300">
                  问盘面、问个股、问功能。
                </div>
                <div className="text-xs leading-relaxed text-zinc-600 dark:text-zinc-400">
                  能查实时行情、K 线与分时、个股资金流、龙虎榜、公告财务、指数与市场宽度、
                  题材梯队、每日精选与持仓；也能讲解各模块用法或回答一般问题。
                </div>
                <div className="mt-2 space-y-1.5">
                  {SUGGESTIONS.map((s) => (
                    <button
                      key={s}
                      type="button"
                      onClick={() => send(s)}
                      className="group flex w-full items-center justify-between rounded-lg border border-zinc-200 px-3 py-2 text-left text-xs text-zinc-600 transition-colors hover:border-zinc-300 hover:bg-zinc-50 dark:border-zinc-800 dark:text-zinc-400 dark:hover:border-zinc-700 dark:hover:bg-zinc-900"
                    >
                      <span>{s}</span>
                      <span
                        aria-hidden
                        className="text-zinc-700 transition-[transform,color] duration-150 ease-out group-hover:translate-x-0.5 group-hover:text-zinc-500 dark:text-zinc-400 dark:group-hover:text-zinc-200"
                      >
                        →
                      </span>
                    </button>
                  ))}
                </div>
              </div>
            )}
            {messages.map((m, i) =>
              m.role === "user" ? (
                // user：面板内唯一的大面积品牌色块（--accent 同源 rose），终点感明确
                <div key={m.id} data-msg-id={m.id} className="flex justify-end">
                  <div className="max-w-[85%] whitespace-pre-wrap rounded-xl rounded-br-sm bg-rose-600 px-3.5 py-2 text-sm text-white">
                    {m.content}
                  </div>
                </div>
              ) : (
                // assistant：去气泡平铺——回复是"内容"不是"卡片"，信息密度与呼吸感兼得；
                // 出错时才给 amber 提示条，正常态零底色
                <div key={m.id} data-msg-id={m.id} className="max-w-[96%]">
                  <div
                    className={
                      m.status === "error"
                        ? "rounded-xl border border-amber-300 bg-amber-50 px-3.5 py-2 text-sm text-zinc-800 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-zinc-200"
                        : "text-sm text-zinc-800 dark:text-zinc-200"
                    }
                  >
                    {m.content ? (
                      <RichText text={m.content} matcher={matcher} onNavigate={onNavigate} />
                    ) : null}
                    {/* 生成中：无正文时显示「思考中…/正在取数：xxx」，有正文时只留光标。
                        光标此前只在 content 为空时出现且极细（2px），用户看不到"在动"。 */}
                    {m.status === "streaming" &&
                      (m.content ? (
                        <span
                          className="ml-0.5 inline-block h-3.5 w-[2px] animate-pulse rounded-full bg-zinc-500 align-[-2px] dark:bg-zinc-400"
                          aria-hidden
                        />
                      ) : (
                        <ActivityLine activity={activity} />
                      ))}
                    {m.status === "interrupted" && (
                      <div className="mt-1 text-[10px] text-zinc-600 dark:text-zinc-400">已停止生成</div>
                    )}
                    {/* 工具回执（P0-3）：这条答案调过哪些只读工具——取过数和没取过
                        必须能一眼分出来，否则"引用了数字"和"编了数字"长得一样 */}
                    {!!m.tools?.length && (
                      <div
                        data-testid="assistant-tools"
                        className="mt-1.5 text-[10px] text-zinc-600 dark:text-zinc-400"
                      >
                        已取数 · {m.tools.join(" / ")}
                      </div>
                    )}
                    {/* 溯源脚注（P0-4）：本条回答引用了哪些源、几点的数据——
                        回答里的每个行情数字都能对上这里的某一行 */}
                    {!!m.sources?.length && (
                      <div
                        data-testid="assistant-sources"
                        className="mt-1.5 border-t border-zinc-200/80 pt-1.5 text-[10px] leading-relaxed text-zinc-600 dark:border-zinc-800/80 dark:text-zinc-400"
                      >
                        <span className="font-medium text-zinc-600 dark:text-zinc-400">数据来源</span>
                        {m.sources.map((s) => (
                          <span key={s.symbol} className="ml-1">
                            · {s.name || s.symbol} {s.source} {s.as_of}
                          </span>
                        ))}
                      </div>
                    )}
                    {i === lastAssistantIdx && m.status !== "streaming" && (
                      <div className="mt-1.5 flex gap-2 text-[10px]">
                        <button
                          type="button"
                          onClick={regenerate}
                          disabled={streaming}
                          className="text-zinc-600 dark:text-zinc-400 underline decoration-dotted underline-offset-2 hover:text-zinc-600 disabled:opacity-40 dark:hover:text-zinc-300"
                        >
                          重新生成
                        </button>
                      </div>
                    )}
                  </div>
                </div>
              ),
            )}
          </div>
          )}

          {/* 输入区 */}
          <div className="border-t border-zinc-200 px-3 py-2.5 dark:border-zinc-800">
            <div className="flex items-end gap-2">
              <textarea
                data-testid="assistant-input"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                    e.preventDefault();
                    send(input);
                  }
                }}
                rows={2}
                placeholder="输入问题，Enter 发送 / Shift+Enter 换行"
                className="max-h-24 min-h-[44px] flex-1 resize-none rounded-xl border border-zinc-200 bg-zinc-50 px-3 py-2 text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-rose-500 focus:outline-none dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100 dark:placeholder:text-zinc-500 dark:focus:border-rose-500"
              />
              {streaming ? (
                <button
                  type="button"
                  onClick={stop}
                  aria-label="停止生成"
                  title="停止生成"
                  className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-zinc-200 text-zinc-600 dark:text-zinc-400 hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-800"
                >
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" aria-hidden>
                    <rect x="5" y="5" width="14" height="14" rx="2" />
                  </svg>
                </button>
              ) : (
                <button
                  type="button"
                  onClick={() => send(input)}
                  disabled={!input.trim()}
                  aria-label="发送"
                  title="发送"
                  className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-rose-600 text-white transition-colors hover:bg-rose-500 disabled:opacity-40"
                >
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                    <path d="M22 2 11 13M22 2l-7 20-4-9-9-4 20-7z" />
                  </svg>
                </button>
              )}
            </div>
            <div className="mt-1.5 text-center text-[10px] text-zinc-600 dark:text-zinc-400">
              AI 生成内容仅供参考，不构成投资建议
            </div>
          </div>
        </div>
      )}
    </>
  );
}
