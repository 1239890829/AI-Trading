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
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { HugeiconsIcon } from "@hugeicons/react";
import ArrowUpRight01Icon from "@hugeicons/core-free-icons/ArrowUpRight01Icon";
import { useInspection } from "@/components/inspection/inspection-context";

import { getEventsForSymbol, getNewsContent, type ArticleBlock, type EventSummary, type EventInterpretationRef, type SelectionEntry } from "@/lib/api";
import { StockAttributionPanel } from "./attribution-evidence";
import { CapitalFlowPanel } from "@/components/detail/capital-flow-panel";
import { bjDate, eventTimeText, fmt, pctText, timeTextBJ } from "@/lib/format";
import { eventDetailPayload, eventDirectionForSymbol, eventDirectionView, eventJudgeView } from "@/lib/event-view";
// 只依赖**零业务依赖**的 context 模块（不 import symbol-detail-modal）：
// 否则形成 detail-modal → symbol-detail-modal → stock-detail → stock-events → detail-modal 的环
import { useSymbolDetail } from "@/components/detail/symbol-detail-context";
import { ModalShell } from "@/components/ui/modal-shell";
import {ArticleBlocks} from "@/components/ui/article-blocks";
import {FactCards, NarrativeText} from "@/components/ui/explanation-map";
import {EventOverview} from "@/components/events/event-overview";
import {ReadingButton} from "@/components/ui/reading-row";
import { useExitPresence } from "@/hooks/use-exit-presence";

export type DetailKind = "news" | "event" | "feed" | "attribution" | "theme" | "capital" | "echelon" | "generic";

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
  /** Exact references from the source list or original selection record, not new inferred history. */
  eventRefs?: EventInterpretationRef[];
  /** Original list snapshot, including every target; never reconstructed from flattened text. */
  eventSnapshot?: EventSummary;
  selectionEntry?: SelectionEntry | null;
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

export function useDetailModal(): DetailCtx {
  return useContext(Ctx);
}

const KIND_LABEL: Record<DetailKind, string> = {
  news: "资讯",
  event: "事件",
  feed: "消息",
  attribution: "驱动依据",
  theme: "题材",
  capital: "资金",
  echelon: "梯队",
  generic: "详情",
};

export function DetailModalProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<{payload: DetailPayload | null; activation: number}>({payload: null, activation: 0});
  const presence = useExitPresence(state.payload);
  const open = useCallback((payload: DetailPayload) => setState(previous => ({payload, activation: previous.activation + 1})), []);
  const close = useCallback(() => setState(previous => ({...previous, payload: null})), []);
  const value = useMemo(() => ({ open, close }), [open, close]);
  return (
    <Ctx.Provider value={value}>
      {children}
      {presence.value ? <DetailModalBody key={state.activation} payload={presence.value} active={presence.active} onClose={close} /> : null}
    </Ctx.Provider>
  );
}

function DetailModalBody({ payload, active, onClose }: { payload: DetailPayload; active: boolean; onClose: () => void }) {
  const { open: inspect } = useInspection();
  const { open } = useDetailModal();
  // 「查看个股详情」→ 标的详情弹窗（2026-09-15 详情弹窗化，原先跳工作台）
  const { open: openSymbolDetail } = useSymbolDetail();
  // 客户端挂载标志与 portal 目标由 `ModalShell` 统一处理（2026-09-15 弹窗外壳统一）
  const [state, setState] = useState<{ status: "idle" | "loading" | "error" | "empty"; blocks?: ArticleBlock[]; truncated?: boolean; msg?: string }>(
    { status: "idle" },
  );
  const [attempt, setAttempt] = useState(0);
  // feed（消息+事件合并列表）：卡片入口用。
  // ⚠️ 2026-09-09 实测两个接口耗时天差地别：events/symbol 0.02s、news/digest **32s**
  // （后者要抓原文+摘要）。此前用 Promise.all 一起等 → 事件块被慢接口白白拖住，
  // 表现为「一直加载中，半天才出」。改为：事件独立快渲染，资讯后台加载并带超时提示。
  const [feedEvents, setFeedEvents] = useState<{ status: "loading" | "ready" | "error"; items: EventSummary[]; error?: string }>({
    status: "loading",
    items: [],
  });
  const isFeed = payload.kind === "feed";
  const isArticle = payload.kind === "news" || payload.kind === "event";
  const selection = payload.selectionEntry;
  const recordedSelection = !!selection && ["recorded", "partial"].includes(selection.state)
    && selection.semantics === "observation_only_not_fill"
    && ["daily_generation", "intraday_rank_archive"].includes(selection.source);
  const selectedAt = recordedSelection && selection.selected_at && /(?:Z|[+-]\d{2}:\d{2})$/.test(selection.selected_at)
    && Number.isFinite(Date.parse(selection.selected_at)) ? selection.selected_at : null;

  // 切股/重试 → 当帧回到「加载中」（渲染期 adjust-state，P1-27）
  const feedKey = `${isFeed ? 1 : 0}:${payload.symbol ?? ""}:${attempt}`;
  const [prevFeedKey, setPrevFeedKey] = useState(feedKey);
  if (feedKey !== prevFeedKey) {
    setPrevFeedKey(feedKey);
    if (isFeed && payload.symbol) setFeedEvents({ status: "loading", items: [] });
  }

  // 事件：快接口（0.02s），先渲染（effect 只负责发起请求，不再同步 setState）
  useEffect(() => {
    if (!active || !isFeed || !payload.symbol) return;
    let alive = true;
    getEventsForSymbol(payload.symbol)
      .then((ev) => alive && setFeedEvents({ status: "ready", items: ev?.items ?? [] }))
      .catch((e: unknown) => alive && setFeedEvents({ status: "error", items: [], error: e instanceof Error ? e.message : "关联事件读取失败" }));
    return () => {
      alive = false;
    };
  }, [active, isFeed, payload.symbol, attempt]);

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
    if (!active || isFeed) return; // feed 走上面的列表分支
    if (!payload.url || (payload.kind !== "news" && payload.kind !== "event")) return;
    let alive = true;
    getNewsContent(payload.url)
      .then((c) => {
        if (!alive) return;
        const blocks: ArticleBlock[] = c?.blocks?.length ? c.blocks : (c?.paragraphs ?? []).map(text => ({type: "p", text}));
        setState(blocks.length > 0 ? { status: "idle", blocks, truncated: !!c.truncated } : { status: "empty", msg: "正文抓取为空（源站未返回内容）" });
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
  }, [active, payload.url, payload.kind, isFeed, attempt]);

  return (
    <ModalShell presentation="drawer"
      open={active}
      onClose={onClose}
      label={payload.title}
      size="md"
      // 内容详情弹窗可**从其他弹窗内**被打开（如概念弹窗里点事件），
      // 故层级高于所有 z-50 弹窗（含标的详情弹窗），与既有实现一致。
      zIndex={60}
      header={
        <>
          <div className="text-[11px] text-zinc-600 dark:text-zinc-400">
            {KIND_LABEL[payload.kind]}
            {payload.date ? ` · ${eventTimeText(payload.date)}` : ""}
            {payload.source ? ` · ${payload.source}` : ""}
          </div>
          <h3 className="mt-0.5 text-sm font-medium text-zinc-800 dark:text-zinc-100">{payload.title}</h3>
        </>
      }
      bodyClassName="overflow-y-auto px-4 py-3"
    >
      {active && <>
        {/* 元信息（判定结果/方向/板块等，跨模块透传） */}
        {payload.eventSnapshot ? <EventOverview event={payload.eventSnapshot} symbol={payload.symbol} /> : payload.meta && payload.meta.length > 0 && <FactCards label="判读记录" items={payload.meta}/>}

        {payload.kind === "attribution" && payload.selectionEntry && <div className="min-w-0 border-b border-zinc-200 pb-2 text-xs dark:border-zinc-700/60" aria-label="原首次加入依据">
          <h3 className="font-medium">原首次加入记录</h3>
          <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-zinc-600 dark:text-zinc-400">
            <span>加入时间 {selectedAt ? <time dateTime={selectedAt}>{bjDate(selectedAt)} {timeTextBJ(selectedAt)}</time> : "未记录"}</span>
            <span className="tabular-nums">参考价 {recordedSelection && typeof selection.reference_price === "number" && Number.isFinite(selection.reference_price) && selection.reference_price > 0 ? `¥${fmt(selection.reference_price)}` : "未记录"}</span>
            {recordedSelection && typeof selection.reference_change_pct === "number" && Number.isFinite(selection.reference_change_pct) && <span className="tabular-nums">当时 {pctText(selection.reference_change_pct)}</span>}
            <span>非成交记录</span>
          </div>
        </div>}
        {(payload.kind === "attribution" || payload.kind === "event") && (payload.symbol || payload.eventId) && <StockAttributionPanel key={`${payload.symbol || ""}:${payload.eventId || ""}`} symbol={payload.symbol} eventId={payload.eventId} defaultExpanded defaultSourceExpanded bounded={false} selectedAt={selectedAt} recordedRefs={payload.eventRefs} selectionContext={payload.kind === "attribution" && !!payload.selectionEntry} title={payload.kind === "event" ? "事件依据核对" : "驱动线索"} />}

        {/* feed（消息+事件合并）：列表 → 点条目切到单条详情 */}
        {isFeed && (
          <div className="mt-3 space-y-3">
            {feedEvents.status === "loading" && <p className="text-xs text-zinc-600 dark:text-zinc-400">关联事件加载中…</p>}
            {feedEvents.status === "error" && <div role="alert" className="text-xs text-amber-800 dark:text-amber-300">
              <p>关联事件读取失败（{feedEvents.error}），尚不能确认是否存在相关事件。</p>
              <button data-action="secondary" className="mt-2" onClick={() => setAttempt(n => n + 1)}>重试关联事件</button>
            </div>}
            {feedEvents.status === "ready" && feedEvents.items.length === 0 && (
              <p className="text-xs text-zinc-600 dark:text-zinc-400">关联事件 · 0 条（今日无与该股题材匹配的活跃事件）</p>
            )}
            {feedEvents.status === "ready" && feedEvents.items.length > 0 && (
              <div>
                <div className="text-[11px] text-zinc-600 dark:text-zinc-400">关联事件 · {feedEvents.items.length} 条</div>
                <ul className="mt-1 space-y-1">
                  {feedEvents.items.map((e) => {
                    const d = eventDirectionForSymbol(e, payload.symbol);
                    const dir = d ? eventDirectionView(d, e, payload.symbol) : null;
                    const judgement = eventJudgeView(e);
                    return (
                      <li key={e.id}>
                        <ReadingButton
                          onClick={() =>
                            open({
                              ...eventDetailPayload(e, payload.symbol),
                              symbol: payload.symbol ?? null,
                              theme: d?.target_type === "theme" ? d.target : payload.theme ?? null,
                            })
                          }
                          className="w-full rounded border border-zinc-200 px-2 py-1 text-left text-[11px] hover:border-zinc-400 dark:border-zinc-700"
                        >
                          <span className="text-zinc-700 dark:text-zinc-200">{e.title}</span>
                          {dir && (
                            <span
                              title={dir.title}
                              className={`ml-1 font-medium ${dir.cls}`}
                            >
                              {dir.text}
                            </span>
                          )}
                          <span title={judgement.title} className={`ml-1 whitespace-nowrap ${judgement.cls}`}>{judgement.text}</span>
                        </ReadingButton>
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
        {payload.body && !payload.eventSnapshot && (
          <NarrativeText text={payload.body} label="原始说明" identity={`${payload.kind}:${payload.eventId ?? payload.title}`}/>
        )}

        {/* 三态：加载 / 错误（可重试）/ 空（显式说明） */}
        {isArticle && <div className="mt-3">
          {state.status === "loading" && <p className="text-xs text-zinc-600 dark:text-zinc-400">正文加载中…</p>}
          {state.status === "error" && (
            <div className="rounded border border-amber-200 bg-amber-50 p-2 text-xs text-amber-800 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-300">
              <p>正文加载失败：{state.msg ?? "未知错误"}</p>
              <button data-action="secondary"
                type="button"
                onClick={() => setAttempt((n) => n + 1)}
                className="mt-1"
              >
                重试
              </button>
            </div>
          )}
          {state.status === "empty" && <p className="text-xs text-zinc-600 dark:text-zinc-400">{state.msg ?? "暂无正文"}</p>}
          {/* 正文自渲染（不嵌套 NewsModal——它自带 portal 遮罩，嵌套会双层弹窗） */}
          {state.status === "idle" && (state.blocks?.length ?? 0) > 0 && (
            <><p className="text-[11px] text-amber-800 dark:text-amber-300" role={state.truncated ? "note" : undefined}>{state.truncated ? "源正文已截断，完整内容请查看原文。" : ""}</p><ArticleBlocks blocks={state.blocks!} identity={payload.url ?? payload.title} title={payload.title} alreadyShownExcerpt={payload.eventSnapshot?.summary ?? undefined}/></>
          )}
        </div>}

        {/* 无原文链接时的兜底说明（此前这类事件直接不可点）；feed 是列表视图不适用 */}
        {!payload.url && isArticle && (
          <p className="mt-2 text-[11px] text-zinc-600 dark:text-zinc-400">
            该事件由快讯流提取，源站未提供原文链接；判定结果见上方元信息。
          </p>
        )}

        {/* 跨模块跳转（唯一真相源：lib/routing） */}
        <div className="mt-3 flex flex-wrap gap-2">
          {payload.symbol && (
            <button data-action="secondary"
              type="button"
              onClick={() => {
                // 先关内容详情再开标的详情：两层同为 z-50 弹窗，叠加会让用户
                // 关掉上层后"还留着一个"（而且 Esc 会同时关两层，行为不一致）。
                onClose();
                openSymbolDetail({ symbol: payload.symbol as string });
              }}
            >
              查看个股详情 {payload.symbol}
            </button>
          )}
          {payload.theme && (
            <button data-action="secondary"
              type="button"
              onClick={() => {
                onClose();
                inspect({kind: "themes", focus: payload.theme as string});
              }}
            >
              查看题材梯队 {payload.theme}
            </button>
          )}
          {payload.url && (
            <a
              data-action="secondary"
              href={payload.url}
              target="_blank"
              rel="noopener noreferrer"
            >
              <span>打开原文</span>
              <HugeiconsIcon icon={ArrowUpRight01Icon} aria-hidden="true" size={14} strokeWidth={1.6} className="shrink-0" />
            </a>
          )}
        </div>
      </>}
    </ModalShell>
  );
}
