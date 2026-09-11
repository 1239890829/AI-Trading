"use client";

/**
 * 通用详情弹窗（2026-09-09 需求 3/4）
 *
 * 背景：此前资讯弹窗（news-modal）、概念弹窗（concept-detail-modal）、精选详情
 * （picks/pick-detail-modal）各写各的，且**个股关联事件无 url 时渲染成不可点的
 * span**——用户点了没反应；加载失败/无数据一律 return null，连个提示都没有。
 *
 * 设计：
 * 1. **Provider 单例** —— 任何模块 `useDetailModal().open(payload)` 即可弹窗，
 *    不必各自维护 modal state；跨模块参数（symbol/theme/eventId/url）走 payload。
 * 2. **统一三态** —— loading / error（带重试）/ empty 都是显式交互，不静默消失。
 * 3. **无 url 兜底** —— 不再因为没链接就不可点；弹窗内显式「原文链接缺失」。
 * 4. **可跳转** —— theme/capital/echelon 类按 nav-targets 单点跳对应功能页。
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useState, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";
import { useRouter } from "next/navigation";

import { getEventsForSymbol, getNewsContent, type ArticleBlock, type EventSummary } from "@/lib/api";
import { CapitalFlowPanel } from "@/components/detail/capital-flow-panel";
import { eventTimeText } from "@/lib/format";
import { workbenchUrlWithBack, themesUrl } from "@/lib/routing";

export type DetailKind = "news" | "event" | "feed" | "theme" | "capital" | "echelon" | "generic";

export interface DetailPayload {
  kind: DetailKind;
  title: string;
  url?: string | null;
  /** 关联个股：决定「去个股页」按钮与正文实体链接上下文 */
  symbol?: string | null;
  /** 关联板块：决定「去题材页」按钮 */
  theme?: string | null;
  /** 事件 id：用于取事件详情（方向行/判定状态） */
  eventId?: number | null;
  source?: string | null;
  date?: string | null;
  /** 附加元信息（如判定状态、利好/利空、产业链） */
  meta?: { label: string; value: string }[];
  /** 正文/摘要文本（通知类无 url 时作为主要内容展示） */
  body?: string | null;
}

interface DetailCtx {
  open: (p: DetailPayload) => void;
  close: () => void;
}

const Ctx = createContext<DetailCtx>({ open: () => {}, close: () => {} });

/** 客户端挂载标志的空订阅（`useSyncExternalStore` 惯用法，P1-27） */
const subscribeNoop = () => () => {};

export function useDetailModal(): DetailCtx {
  return useContext(Ctx);
}

const KIND_LABEL: Record<DetailKind, string> = {
  news: "资讯",
  event: "事件",
  feed: "消息",
  theme: "题材",
  capital: "资金",
  echelon: "梯队",
  generic: "详情",
};

export function DetailModalProvider({ children }: { children: React.ReactNode }) {
  const [payload, setPayload] = useState<DetailPayload | null>(null);
  const open = useCallback((p: DetailPayload) => setPayload(p), []);
  const close = useCallback(() => setPayload(null), []);
  const value = useMemo(() => ({ open, close }), [open, close]);
  return (
    <Ctx.Provider value={value}>
      {children}
      {payload ? <DetailModalBody payload={payload} onClose={close} /> : null}
    </Ctx.Provider>
  );
}

function DetailModalBody({ payload, onClose }: { payload: DetailPayload; onClose: () => void }) {
  const router = useRouter();
  const { open } = useDetailModal();
  // 客户端挂载标志（SSR/hydration 安全）：`useSyncExternalStore` 取代
  // `useEffect(() => setMounted(true), [])`——服务端快照 false、客户端 true，
  // 渲染结果与 effect 版一致（首帧 null → 挂载后渲染内容），但不在 effect 里
  // 同步 setState（P1-27）。
  const mounted = useSyncExternalStore(subscribeNoop, () => true, () => false);
  const [state, setState] = useState<{ status: "idle" | "loading" | "error" | "empty"; blocks?: ArticleBlock[]; msg?: string }>(
    { status: "idle" },
  );
  const [attempt, setAttempt] = useState(0);
  // feed（消息+事件合并列表）：卡片入口用。
  // ⚠️ 2026-09-09 实测两个接口耗时天差地别：events/symbol 0.02s、news/digest **32s**
  // （后者要抓原文+摘要）。此前用 Promise.all 一起等 → 事件块被慢接口白白拖住，
  // 表现为「一直加载中，半天才出」。改为：事件独立快渲染，资讯后台加载并带超时提示。
  const [feedEvents, setFeedEvents] = useState<{ status: "loading" | "ready"; items: EventSummary[] }>({
    status: "loading",
    items: [],
  });
  const isFeed = payload.kind === "feed";

  // 切股/重试 → 当帧回到「加载中」（渲染期 adjust-state，P1-27）
  const feedKey = `${isFeed ? 1 : 0}:${payload.symbol ?? ""}:${attempt}`;
  const [prevFeedKey, setPrevFeedKey] = useState(feedKey);
  if (feedKey !== prevFeedKey) {
    setPrevFeedKey(feedKey);
    if (isFeed && payload.symbol) setFeedEvents({ status: "loading", items: [] });
  }

  // 事件：快接口（0.02s），先渲染（effect 只负责发起请求，不再同步 setState）
  useEffect(() => {
    if (!isFeed || !payload.symbol) return;
    let alive = true;
    getEventsForSymbol(payload.symbol)
      .then((ev) => alive && setFeedEvents({ status: "ready", items: ev?.items ?? [] }))
      .catch(() => alive && setFeedEvents({ status: "ready", items: [] }));
    return () => {
      alive = false;
    };
  }, [isFeed, payload.symbol, attempt]);

  // 2026-09-09 需求 1：**不再单独拉取个股全部资讯**。
  // 猎场/卡片的「消息」定位 = 与该股**入选相关**的消息与事件（即 EventStore 里
  // 命中该股题材的事件）；个股全量资讯（news/digest，实测 ~32s）属于个股详情页
  // 的能力，不在这里重复建设——既慢又与入选逻辑无关。

  // 正文抓取：仅资讯/事件类且有 url 时才抓；无 url 直接进 empty 兜底（不假成功）。
  // 分支状态在**渲染期**一次定好（P1-27）：旧写法在 effect 里同步 setState，
  // 首帧会残留上一份 payload 的正文。prevContentKey 用 null 起手，保证首帧也执行。
  const contentKey = `${payload.kind}:${payload.url ?? ""}:${attempt}`;
  const [prevContentKey, setPrevContentKey] = useState<string | null>(null);
  if (contentKey !== prevContentKey) {
    setPrevContentKey(contentKey);
    if (!isFeed) {
      setState(!payload.url || (payload.kind !== "news" && payload.kind !== "event")
        ? { status: "empty", msg: "原文链接缺失——仅展示事件摘要与判定结果" }
        : { status: "loading" });
    }
  }

  useEffect(() => {
    if (isFeed) return; // feed 走上面的列表分支
    if (!payload.url || (payload.kind !== "news" && payload.kind !== "event")) return;
    let alive = true;
    getNewsContent(payload.url)
      .then((c) => {
        if (!alive) return;
        const blocks = (c?.blocks ?? []) as ArticleBlock[];
        setState(blocks.length > 0 ? { status: "idle", blocks } : { status: "empty", msg: "正文抓取为空（源站未返回内容）" });
      })
      .catch((e: unknown) => {
        if (!alive) return;
        setState({ status: "error", msg: e instanceof Error ? e.message : "正文加载失败" });
      });
    return () => {
      alive = false;
    };
    // isFeed 由 payload.kind 派生（见上方 const），补进依赖只是让 lint 显式可见，
    // 实际重跑时机与 payload.kind 完全一致，行为不变。
  }, [payload.url, payload.kind, isFeed, attempt]);

  // Esc 关闭（与全站弹窗一致）
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  if (!mounted) return null;

  return createPortal(
    <div
      className="anim-backdrop-in fixed inset-0 z-[60] flex items-center justify-center bg-black/50 p-4"
      onClick={onClose}
      role="presentation"
    >
      <div
        className="anim-scale-in max-h-[85vh] w-full max-w-2xl overflow-y-auto rounded-xl bg-white p-4 shadow-xl dark:bg-zinc-900"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label={payload.title}
      >
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="text-[11px] text-zinc-600 dark:text-zinc-400">
              {KIND_LABEL[payload.kind]}
              {payload.date ? ` · ${eventTimeText(payload.date)}` : ""}
              {payload.source ? ` · ${payload.source}` : ""}
            </div>
            <h3 className="mt-0.5 text-sm font-medium text-zinc-800 dark:text-zinc-100">{payload.title}</h3>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="shrink-0 rounded border border-zinc-200 px-2 py-0.5 text-xs text-zinc-600 dark:text-zinc-400 hover:border-zinc-400 dark:border-zinc-700"
          >
            关闭
          </button>
        </div>

        {/* 元信息（判定结果/方向/板块等，跨模块透传） */}
        {payload.meta && payload.meta.length > 0 && (
          <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-[11px]">
            {payload.meta.map((m) => (
              <div key={m.label} className="flex gap-1">
                <dt className="shrink-0 text-zinc-600 dark:text-zinc-400">{m.label}</dt>
                <dd className="min-w-0 truncate text-zinc-700 dark:text-zinc-200">{m.value}</dd>
              </div>
            ))}
          </dl>
        )}

        {/* feed（消息+事件合并）：列表 → 点条目切到单条详情 */}
        {isFeed && (
          <div className="mt-3 space-y-3">
            {feedEvents.status === "loading" && <p className="text-xs text-zinc-600 dark:text-zinc-400">关联事件加载中…</p>}
            {feedEvents.status === "ready" && feedEvents.items.length === 0 && (
              <p className="text-xs text-zinc-600 dark:text-zinc-400">关联事件 · 0 条（今日无与该股题材匹配的活跃事件）</p>
            )}
            {feedEvents.status === "ready" && feedEvents.items.length > 0 && (
              <div>
                <div className="text-[11px] text-zinc-600 dark:text-zinc-400">关联事件 · {feedEvents.items.length} 条</div>
                <ul className="mt-1 space-y-1">
                  {feedEvents.items.map((e) => {
                    const d = e.directions?.[0];
                    return (
                      <li key={e.id}>
                        <button
                          type="button"
                          onClick={() =>
                            open({
                              kind: "event",
                              title: e.title,
                              url: e.url ?? null,
                              symbol: payload.symbol ?? null,
                              theme: d?.target ?? payload.theme ?? null,
                              source: e.source ?? null,
                              date: e.published_at ?? null,
                              body: e.summary ?? null,
                              meta: [
                                ...(d ? [{ label: "判定", value: `${d.direction > 0 ? "利好" : d.direction < 0 ? "利空" : "待判"}${e.judge_status_label && e.judge_status_label !== (d.direction > 0 ? "利好" : d.direction < 0 ? "利空" : "待判") ? `（${e.judge_status_label}）` : ""}` }] : []),
                                ...(d?.basis ? [{ label: "依据", value: d.basis }] : []),
                              ],
                            })
                          }
                          className="w-full rounded border border-zinc-200 px-2 py-1 text-left text-[11px] hover:border-zinc-400 dark:border-zinc-700"
                        >
                          <span className="text-zinc-700 dark:text-zinc-200">{e.title}</span>
                          {d && (
                            <span
                              className={`ml-1 font-medium ${d.direction > 0 ? "text-up-ink dark:text-up" : d.direction < 0 ? "text-down-ink dark:text-down" : "text-zinc-600 dark:text-zinc-400"}`}
                            >
                              {d.direction > 0 ? "利好" : d.direction < 0 ? "利空" : "待判"}
                            </span>
                          )}
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </div>
            )}
            {/* 口径说明：此处只列「入选相关」的事件；个股全量资讯在个股页「消息」页签 */}
            <p className="mt-2 text-[11px] text-zinc-600 dark:text-zinc-400">
              口径：与该股题材命中的活跃事件（EventStore 关联），不含个股全量资讯——
              全量资讯请在个股页查看。
            </p>
          </div>
        )}

        {/* 资金：复用工作台资金图（自治组件，按 symbol 取数），弹窗内直接展示不跳转 */}
        {payload.kind === "capital" && payload.symbol && (
          <div className="mt-3">
            <CapitalFlowPanel symbol={payload.symbol} />
          </div>
        )}

        {/* 正文/摘要文本（无 url 内容的主载体，如通知类快讯） */}
        {payload.body && (
          <p className="mt-2 whitespace-pre-wrap text-xs leading-relaxed text-zinc-700 dark:text-zinc-300">
            {payload.body}
          </p>
        )}

        {/* 三态：加载 / 错误（可重试）/ 空（显式说明） */}
        <div className="mt-3">
          {state.status === "loading" && <p className="text-xs text-zinc-600 dark:text-zinc-400">正文加载中…</p>}
          {state.status === "error" && (
            <div className="rounded border border-amber-200 bg-amber-50 p-2 text-xs text-amber-800 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-300">
              <p>正文加载失败：{state.msg ?? "未知错误"}</p>
              <button
                type="button"
                onClick={() => setAttempt((n) => n + 1)}
                className="mt-1 rounded border border-amber-300 px-2 py-0.5 text-[11px] hover:bg-amber-100 dark:border-amber-800"
              >
                重试
              </button>
            </div>
          )}
          {state.status === "empty" && <p className="text-xs text-zinc-600 dark:text-zinc-400">{state.msg ?? "暂无正文"}</p>}
          {/* 正文自渲染（不嵌套 NewsModal——它自带 portal 遮罩，嵌套会双层弹窗） */}
          {state.status === "idle" && (state.blocks?.length ?? 0) > 0 && (
            <div className="space-y-2 text-xs leading-relaxed text-zinc-700 dark:text-zinc-300">
              {state.blocks!.map((b, i) =>
                b.type === "p" ? (
                  <p key={i}>{b.text}</p>
                ) : b.type === "table" ? (
                  <div key={i} className="overflow-x-auto">
                    <table className="w-full border-collapse text-[11px]">
                      <tbody>
                        {b.rows.map((row, ri) => (
                          <tr key={ri} className={ri % 2 ? "bg-zinc-50 dark:bg-zinc-800/50" : ""}>
                            {row.map((cell, ci) => (
                              <td key={ci} className="border border-zinc-200 px-1.5 py-1 dark:border-zinc-700">
                                {cell}
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : null,
              )}
            </div>
          )}
        </div>

        {/* 无原文链接时的兜底说明（此前这类事件直接不可点）；feed 是列表视图不适用 */}
        {!payload.url && !isFeed && (
          <p className="mt-2 text-[11px] text-zinc-600 dark:text-zinc-400">
            该事件由快讯流提取，源站未提供原文链接；判定结果见上方元信息。
          </p>
        )}

        {/* 跨模块跳转（唯一真相源：lib/routing） */}
        <div className="mt-3 flex flex-wrap gap-2">
          {payload.symbol && (
            <button
              type="button"
              onClick={() => {
                onClose();
                router.push(workbenchUrlWithBack(payload.symbol as string));
              }}
              className="rounded border border-zinc-200 px-2 py-1 text-[11px] text-zinc-600 hover:border-zinc-400 dark:border-zinc-700 dark:text-zinc-300"
            >
              去个股页 {payload.symbol}
            </button>
          )}
          {payload.theme && (
            <button
              type="button"
              onClick={() => {
                onClose();
                router.push(themesUrl(payload.theme as string));
              }}
              className="rounded border border-zinc-200 px-2 py-1 text-[11px] text-zinc-600 hover:border-zinc-400 dark:border-zinc-700 dark:text-zinc-300"
            >
              去题材页 {payload.theme}
            </button>
          )}
          {payload.url && (
            <a
              href={payload.url}
              target="_blank"
              rel="noopener noreferrer"
              className="rounded border border-zinc-200 px-2 py-1 text-[11px] text-zinc-600 hover:border-zinc-400 dark:border-zinc-700 dark:text-zinc-300"
            >
              打开原文 ↗
            </a>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}
