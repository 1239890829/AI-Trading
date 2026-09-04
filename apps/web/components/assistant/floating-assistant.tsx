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
import { API_BASE } from "@/lib/api";
import { workbenchUrl, themesUrl } from "@/lib/routing";
import { createEntityMatcher, type EntityDict, type EntityMatch } from "@/lib/entity-links";
import { RichText } from "@/components/assistant/rich-text";

const BALL = 48;
const MARGIN = 16;
const POS_KEY = "ashare.assistant.pos";
const PANEL_W = 400;
const PANEL_H = 560;

const PAGE_TITLES: Record<string, string> = {
  "/workbench": "工作台",
  "/tape": "盘面",
  "/market": "市场",
  "/picks": "每日精选",
  "/intraday": "盘中跟踪",
  "/research": "研究",
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
            if (ev.type === "meta" && typeof ev.model === "string") {
              setModel(ev.model);
            } else if (ev.type === "delta" && typeof ev.text === "string") {
              setMessages(syncRef((prev) => {
                if (!prev.length) return prev;
                const next = [...prev];
                const lastMsg = next[next.length - 1];
                next[next.length - 1] = { ...lastMsg, content: lastMsg.content + (ev.text as string) };
                return next;
              }));
            } else if (ev.type === "error" && typeof ev.message === "string") {
              patchLast({ status: "error", content: `⚠️ ${ev.message}` });
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
  const onNavigate = (m: EntityMatch) => {
    router.push(m.type === "stock" && m.code ? workbenchUrl(m.code) : themesUrl(m.name));
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
      {/* 悬浮球 */}
      <div
        data-testid="assistant-ball"
        role="button"
        aria-label="AI 助手"
        className="fixed z-50 flex cursor-grab select-none items-center justify-center rounded-full border border-sky-400/30 bg-gradient-to-br from-sky-500 to-violet-600 text-white shadow-lg shadow-sky-500/25 transition-shadow hover:shadow-xl hover:shadow-sky-500/40 active:cursor-grabbing"
        style={{ left: pos.x, top: pos.y, width: BALL, height: BALL }}
        onPointerDown={onBallPointerDown}
        onPointerMove={onBallPointerMove}
        onPointerUp={onBallPointerUp}
      >
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
          <path d="M12 3a7 7 0 0 1 7 7c0 2.5-1.4 4.3-3 5.5V18a2 2 0 0 1-2 2h-4a2 2 0 0 1-2-2v-2.5C6.4 14.3 5 12.5 5 10a7 7 0 0 1 7-7z" />
          <path d="M10 21h4" />
        </svg>
        {streaming && !open && (
          <span className="absolute -right-0.5 -top-0.5 h-3 w-3 animate-pulse rounded-full border-2 border-white bg-emerald-400" />
        )}
      </div>

      {/* 聊天窗 */}
      {open && (
        <div
          data-testid="assistant-panel"
          className="fixed z-50 flex flex-col overflow-hidden rounded-2xl border border-zinc-200 bg-white/95 shadow-2xl backdrop-blur dark:border-zinc-800 dark:bg-zinc-950/95"
          style={panelStyle}
        >
          {/* 头部 */}
          <div className="flex items-center gap-2 border-b border-zinc-200 px-4 py-2.5 dark:border-zinc-800">
            <span className="flex h-6 w-6 items-center justify-center rounded-full bg-gradient-to-br from-sky-500 to-violet-600 text-white">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                <path d="M12 3a7 7 0 0 1 7 7c0 2.5-1.4 4.3-3 5.5V18a2 2 0 0 1-2 2h-4a2 2 0 0 1-2-2v-2.5C6.4 14.3 5 12.5 5 10a7 7 0 0 1 7-7z" />
              </svg>
            </span>
            <div className="min-w-0 flex-1">
              <div className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">AI 助手</div>
              {model && (
                <div className="truncate text-[10px] text-zinc-400 dark:text-zinc-500">{model}</div>
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
            className="min-h-0 flex-1 space-y-3 overflow-y-auto px-4 py-3"
          >
            {messages.length === 0 && (
              <div className="flex h-full flex-col items-center justify-center gap-4 text-center">
                <div className="text-sm text-zinc-500 dark:text-zinc-400">
                  我是本工作台的 AI 助手，可以讲解各模块功能、
                  <br />
                  聊板块与个股（无实时行情），或回答一般问题。
                </div>
                <div className="flex flex-wrap justify-center gap-2">
                  {SUGGESTIONS.map((s) => (
                    <button
                      key={s}
                      type="button"
                      onClick={() => send(s)}
                      className="rounded-full border border-zinc-200 px-3 py-1.5 text-xs text-zinc-600 transition-colors hover:border-sky-400 hover:text-sky-600 dark:border-zinc-700 dark:text-zinc-400 dark:hover:border-sky-500 dark:hover:text-sky-400"
                    >
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {messages.map((m, i) => (
              <div key={m.id} className={m.role === "user" ? "flex justify-end" : "flex justify-start"}>
                <div
      className={
        m.role === "user"
          ? "max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-md bg-sky-600 px-3 py-2 text-sm text-white"
          : `max-w-[92%] rounded-2xl rounded-bl-md px-3 py-2 text-sm text-zinc-800 dark:text-zinc-200 ${
              m.status === "error"
                ? "border border-amber-300 bg-amber-50 dark:border-amber-500/40 dark:bg-amber-500/10"
                : "bg-zinc-100 dark:bg-zinc-900"
            }`
      }
                >
                  {m.role === "assistant" && m.content ? (
                    <RichText text={m.content} matcher={matcher} onNavigate={onNavigate} />
                  ) : (
                    m.content
                  )}
                  {m.role === "assistant" && m.status === "streaming" && !m.content && (
                    <span className="inline-flex gap-1 py-1" aria-label="正在思考">
                      <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-zinc-400 [animation-delay:0ms]" />
                      <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-zinc-400 [animation-delay:150ms]" />
                      <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-zinc-400 [animation-delay:300ms]" />
                    </span>
                  )}
                  {m.role === "assistant" && m.status === "interrupted" && (
                    <div className="mt-1 text-[10px] text-zinc-400 dark:text-zinc-500">已停止生成</div>
                  )}
                  {m.role === "assistant" && i === lastAssistantIdx && m.status !== "streaming" && (
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
            ))}
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
                className="max-h-24 min-h-[44px] flex-1 resize-none rounded-xl border border-zinc-200 bg-zinc-50 px-3 py-2 text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-sky-400 focus:outline-none dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100 dark:placeholder:text-zinc-500 dark:focus:border-sky-500"
              />
              {streaming ? (
                <button
                  type="button"
                  onClick={stop}
                  aria-label="停止生成"
                  title="停止生成"
                  className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-zinc-200 text-zinc-500 hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-800"
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
                  className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-sky-600 text-white transition-opacity hover:bg-sky-500 disabled:opacity-40"
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
