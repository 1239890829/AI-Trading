"use client";

/**
 * 全局 AI 助手浮窗（所有模块页面可用）。
 *
 * - 悬浮球：pointer 拖动、松手吸附左右边缘、位置 localStorage 持久化；
 *   「移动 < 4px」判定为点击（展开/收起），避免拖动误触
 * - 聊天窗：SSE 流式渲染、中断（AbortController）/重新生成、最小化收回悬浮球
 * - 实体跳转：回答中的个股/题材经 entity-dict 词典识别 → 点击跳工作台详情/题材梯队
 * - 上下文：发送时带上当前页面 path/title/选中标的，后端注入系统提示
 *
 * 挂载在 app/layout.tsx（NavBar 之后），z-50 盖过导航（z-40）。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { API_BASE, type AgentBubble } from "@/lib/api";
import { workbenchUrl, themesUrl } from "@/lib/routing";
import { createEntityMatcher, type EntityDict, type EntityMatch } from "@/lib/entity-links";
import { isAllowedNav } from "@/lib/nav-targets";
import { RichText } from "@/components/assistant/rich-text";
import { AssistantMark } from "@/components/assistant/assistant-mark";

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
  "/agent": "AI 控制台",
};

const SUGGESTIONS = [
  "工作台有哪些功能？",
  "每日精选的选股逻辑是什么？",
  "题材梯队怎么看？",
];

interface ChatMsg {
  id: number;
  role: "user" | "assistant";
  content: string;
  status: "ok" | "streaming" | "interrupted" | "error";
  /** 本条回答引用到的实时快照溯源（来源 + 数据时间）；无快照为空 */
  sources?: { symbol: string; name: string; source: string; as_of: string }[];
  /** 本条回答实际调用过的工具名（后端白名单内的只读工具） */
  tools?: string[];
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
  const [mounted, setMounted] = useState(false);
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ x: number; y: number } | null>(null);
  const [viewport, setViewport] = useState({ w: 1280, h: 800 });
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [model, setModel] = useState("");
  const [dict, setDict] = useState<EntityDict | null>(null);
  const [bubbles, setBubbles] = useState<AgentBubble[]>([]);
  const [docked, setDocked] = useState<"left" | "right" | null>(null);
  const [orbHovered, setOrbHovered] = useState(false);

  const abortRef = useRef<AbortController | null>(null);
  const idRef = useRef(0);
  const msgsRef = useRef<ChatMsg[]>([]);
  const dictTriedRef = useRef(false);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const stickRef = useRef(true);

  const nextId = () => ++idRef.current;
  const syncRef = (updater: (prev: ChatMsg[]) => ChatMsg[]) => {
    msgsRef.current = updater(msgsRef.current);
    return msgsRef.current;
  };

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
    const onResize = () => {
      setViewport({ w: window.innerWidth, h: window.innerHeight });
      setPos((p) => (p ? clampPos(p) : p));
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  // ---- AI 判读提醒（悬浮球气泡）------------------------------------------
  // 只有判读为 notify 且未确认的才出现；规则触发本身不冒泡（防刷屏）。
  // 30s 轮询：告警不是秒级决策，且 triage worker 本身也是 30s 一轮。
  useEffect(() => {
    if (!mounted) return;
    let alive = true;
    async function loadBubbles() {
      try {
        const { getAgentBubbles } = await import("@/lib/api");
        const list = await getAgentBubbles(5);
        if (alive) setBubbles(list);
      } catch {
        if (alive) setBubbles([]);   // 后端未起/接口异常：静默，不打扰
      }
    }
    void loadBubbles();
    const id = setInterval(() => void loadBubbles(), 30_000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [mounted]);

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
    const assistantId = nextId();
    setMessages(syncRef((prev) => [
      ...prev,
      { id: assistantId, role: "assistant", content: "", status: "streaming" },
    ]));
    setStreaming(true);
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
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let idx: number;
        while ((idx = buf.indexOf("\n\n")) >= 0) {
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
            } else if (ev.type === "tools" && Array.isArray(ev.used)) {
              // 工具调用回执：让用户知道这条答案取过数，不是模型凭空编的
              const used = (ev.used as unknown[]).map(String);
              setMessages(syncRef((prev) => {
                if (!prev.length) return prev;
                const next = [...prev];
                next[next.length - 1] = { ...next[next.length - 1], tools: used };
                return next;
              }));
            } else if (ev.type === "delta" && typeof ev.text === "string") {
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
      abortRef.current = null;
      setStreaming(false);
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

  const clearChat = () => {
    if (streaming) abortRef.current?.abort();
    setMessages(syncRef(() => []));
  };

  // ---- 跳转 ----------------------------------------------------------------
  // 三类落点：个股 → 工作台；题材 → 盘面题材梯队；功能入口 → 注册表给出的站内深链。
  // nav 的 URL 在识别阶段已过白名单守卫，这里再过一次（防御渲染期被篡改），
  // 未过则降级为跳题材页，绝不 push 非常规 URL。
  const onNavigate = (m: EntityMatch) => {
    if (m.type === "nav") {
      const url = m.url && isAllowedNav(m.url) ? m.url : themesUrl(m.name);
      router.push(url);
    } else {
      router.push(m.type === "stock" && m.code ? workbenchUrl(m.code) : themesUrl(m.name));
    }
    setOpen(false);
  };

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
          墨玉半胶囊把手贴在屏幕边缘（rose 细竖条提示），hover 平滑展开为完整球 */}
      <div
        data-testid="assistant-ball"
        role="button"
        aria-label="AI 助手"
        className={`fixed z-50 flex cursor-grab select-none items-center justify-center bg-zinc-900 text-zinc-50 shadow-[0_2px_8px_rgba(0,0,0,0.18),0_10px_28px_rgba(0,0,0,0.22)] transition-[left,width,height,border-radius,box-shadow] duration-200 ease-out hover:shadow-[0_4px_12px_rgba(0,0,0,0.22),0_14px_36px_rgba(0,0,0,0.28)] active:cursor-grabbing dark:bg-zinc-100 dark:text-zinc-950 dark:shadow-[0_2px_8px_rgba(0,0,0,0.4),0_10px_28px_rgba(0,0,0,0.35)] ${
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

      {/* 提醒气泡：AI 判读后才出现（规则触发 ≠ 值得提醒）；点开进控制台告警页 */}
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
              <span className="rounded bg-amber-500/10 px-1 py-0.5 text-[10px] text-amber-600 dark:text-amber-300">
                按规则提醒
              </span>
            )}
          </div>
          <p className="line-clamp-2 text-[11px] leading-relaxed text-zinc-600 dark:text-zinc-300">
            {bubbles[0].symbol ? `${bubbles[0].symbol} · ` : ""}
            {bubbles[0].reason || "触发告警"}
          </p>
          <div className="mt-2 flex items-center gap-1.5">
            <button
              type="button"
              onClick={() => router.push("/agent?tab=alerts")}
              className="rounded-md border border-zinc-300 px-2 py-0.5 text-[11px] text-zinc-700 hover:bg-zinc-100 dark:border-zinc-600 dark:text-zinc-200 dark:hover:bg-zinc-800"
            >
              查看
            </button>
            <button
              type="button"
              onClick={() => void ackBubble(bubbles[0].id)}
              className="rounded-md px-2 py-0.5 text-[11px] text-zinc-500 hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-800"
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
            <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-zinc-900 text-zinc-50 dark:bg-zinc-100 dark:text-zinc-950">
              <AssistantMark size={15} strokeWidth={2} />
            </span>
            <div className="min-w-0 flex-1">
              <div className="text-[13px] font-semibold tracking-tight text-zinc-900 dark:text-zinc-100">AI 助手</div>
              {model && (
                <div className="truncate font-mono text-[10px] leading-tight text-zinc-400 dark:text-zinc-500">{model}</div>
              )}
            </div>
            {messages.length > 0 && (
              <button
                type="button"
                aria-label="清空对话"
                title="清空对话"
                onClick={clearChat}
                className="rounded-md p-1.5 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-600 dark:hover:bg-zinc-800 dark:hover:text-zinc-300"
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                  <path d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2m3 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6" />
                </svg>
              </button>
            )}
            <button
              type="button"
              aria-label="最小化"
              title="最小化"
              onClick={() => setOpen(false)}
              className="rounded-md p-1.5 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-600 dark:hover:bg-zinc-800 dark:hover:text-zinc-300"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden>
                <path d="M5 12h14" />
              </svg>
            </button>
          </div>

          {/* 消息区 */}
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
                <div className="text-xs leading-relaxed text-zinc-400 dark:text-zinc-500">
                  可以讲解各模块用法、聊板块与个股（无实时行情），或回答一般问题。
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
                        className="text-zinc-300 transition-[transform,color] duration-150 ease-out group-hover:translate-x-0.5 group-hover:text-zinc-500 dark:text-zinc-600 dark:group-hover:text-zinc-400"
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
                <div key={m.id} className="flex justify-end">
                  <div className="max-w-[85%] whitespace-pre-wrap rounded-xl rounded-br-sm bg-rose-600 px-3.5 py-2 text-sm text-white">
                    {m.content}
                  </div>
                </div>
              ) : (
                // assistant：去气泡平铺——回复是"内容"不是"卡片"，信息密度与呼吸感兼得；
                // 出错时才给 amber 提示条，正常态零底色
                <div key={m.id} className="max-w-[96%]">
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
                    {m.status === "streaming" && !m.content && (
                      <span
                        className="inline-block h-4 w-0.5 animate-pulse rounded-full bg-zinc-400 dark:bg-zinc-500"
                        aria-label="正在思考"
                      />
                    )}
                    {m.status === "interrupted" && (
                      <div className="mt-1 text-[10px] text-zinc-400 dark:text-zinc-500">已停止生成</div>
                    )}
                    {/* 工具回执（P0-3）：这条答案调过哪些只读工具——取过数和没取过
                        必须能一眼分出来，否则"引用了数字"和"编了数字"长得一样 */}
                    {!!m.tools?.length && (
                      <div
                        data-testid="assistant-tools"
                        className="mt-1.5 text-[10px] text-zinc-400 dark:text-zinc-500"
                      >
                        已取数 · {m.tools.join(" / ")}
                      </div>
                    )}
                    {/* 溯源脚注（P0-4）：本条回答引用了哪些源、几点的数据——
                        回答里的每个行情数字都能对上这里的某一行 */}
                    {!!m.sources?.length && (
                      <div
                        data-testid="assistant-sources"
                        className="mt-1.5 border-t border-zinc-200/80 pt-1.5 text-[10px] leading-relaxed text-zinc-400 dark:border-zinc-800/80 dark:text-zinc-500"
                      >
                        <span className="font-medium text-zinc-500 dark:text-zinc-400">数据来源</span>
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
                          className="text-zinc-400 underline decoration-dotted underline-offset-2 hover:text-zinc-600 disabled:opacity-40 dark:hover:text-zinc-300"
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
                  className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-zinc-200 text-zinc-500 hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-800"
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
            <div className="mt-1.5 text-center text-[10px] text-zinc-400 dark:text-zinc-600">
              AI 生成内容仅供参考，不构成投资建议
            </div>
          </div>
        </div>
      )}
    </>
  );
}
