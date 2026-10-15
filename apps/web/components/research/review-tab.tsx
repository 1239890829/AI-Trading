"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Panel } from "@/components/panel";
import { useResource } from "@/hooks/use-polling-fetch";
import {ConditionList, ExplanationMap, NarrativeText} from "@/components/ui/explanation-map";
import {readingExcerpt} from "@/lib/reading-clues";
import "./review-layout.css";
import {
  ApiError,
  getReviewEffectiveness,
  getReviewReport,
  getReviewReports,
  updateActionItemStatus,
  type ActionItemRef,
  type ActionItemStatus,
  type ReviewReportDetail,
  type ReviewReportSummary,
} from "@/lib/api";

/**
 * 复盘 tab（评审 A2，2026-09-01）：方法论闭环的报告与有效性数据此前只在
 * 后端 6 个端点产出，前端无查看入口（每日精选页的复盘区只展示 picks 归因，
 * 不覆盖这套"报告→改进项→采纳统计"闭环）。
 *
 * 口径说明：复盘报告由后端每日盘后生成（/review/run），记录当日操作评估、
 * 数据缺口与改进项；effectiveness 统计各类别改进项的采纳/回退率——
 * 采纳率仅是管理处置事实，不代表交易效果。
 */

const PRIORITY_CLS: Record<string, string> = {
  P0: "text-red-700 dark:text-red-400",
  P1: "text-amber-800 dark:text-amber-400",
  P2: "text-zinc-600 dark:text-zinc-400",
};

const CATEGORY_LABEL: Record<string, string> = {
  data: "数据",
  process: "流程",
  strategy: "策略",
  risk: "风控",
  other: "其他",
};

const STATUS_LABEL: Record<string, string> = {
  pending: "待处置",
  recorded: "已记录 · 待实施",
  confirmed: "已确认",
  applied: "已实施",
  rejected: "已驳回",
  reverted: "已回退",
};

const STATUS_CLS: Record<string, string> = {
  recorded: "text-zinc-600 dark:text-zinc-400",
  pending: "text-zinc-600 dark:text-zinc-400",
  confirmed: "text-sky-700 dark:text-sky-400",
  applied: "text-emerald-700 dark:text-emerald-400",
  rejected: "text-zinc-600 dark:text-zinc-400 line-through",
  reverted: "text-amber-800 dark:text-amber-400",
};

/** 需要填写理由才能提交的状态——后端强制校验，前端同步提示，避免点了才报错。 */
const NOTE_REQUIRED: ActionItemStatus[] = ["applied", "rejected", "reverted"];

/** gaps 元素可能是字符串，也可能是结构化对象（{field,reason,impact,severity}）——统一成可读文本。 */
function gapText(g: unknown): string {
  if (typeof g === "string") return g;
  if (g && typeof g === "object") {
    const o = g as Record<string, unknown>;
    const parts = [
      o.field ? String(o.field) : null,
      o.reason ? String(o.reason) : null,
      o.impact ? `影响 ${o.impact}` : null,
      o.severity ? `严重度 ${o.severity}` : null,
    ].filter(Boolean);
    return parts.join("：") || JSON.stringify(o);
  }
  return String(g);
}

// Free-text numbers have no verified entity identity. Readers preserve them as text;
// explicit stock links remain in their source records.

// Visibility reads do not set Resource.pending. Disposition also needs to wait
// for those reads; keep this owner fact local to the two sources it consumes.
function useDispositionResource<T>(fn: () => Promise<T>, key: unknown, enabled = true) {
  const sequence = useRef(0);
  const alive = useRef(false);
  const [read, setRead] = useState({ key, pending: false });
  useEffect(() => {
    alive.current = enabled;
    return () => { alive.current = false; sequence.current += 1; };
  }, [key, enabled]);
  const resource = useResource(async () => {
    const current = ++sequence.current;
    setRead({ key, pending: true });
    try {
      return await fn();
    } finally {
      if (alive.current && current === sequence.current) setRead({ key, pending: false });
    }
  }, { key, enabled, intervalMs: null, marketHours: false });
  const inFlight = Object.is(read.key, key) && read.pending;
  return { ...resource, inFlight, reading: resource.pending || inFlight };
}

/**
 * 单条改进项的处置控件。
 *
 * 改进项若只能看不能处置，PDCA 闭环就断在最后一环——2026-09-01 核查时
 * 107 条改进项全部 pending、采纳率 0%，根因就是缺这个入口。
 *
 * `item` 除 id 外还带 (trade_date, category, title) 守卫三元组：报告重跑后
 * id 会漂移（rowid 复用），裸 id 处置会静默挂到不相干的改进项上；
 * 守卫不符时后端返回 409，error 展示"请刷新后重试"。
 */
function ActionItemDispose({
  item,
  status,
  onDisposed,
  disabled = false,
}: {
  item: ActionItemRef;
  status: string;
  onDisposed: () => void;
  disabled?: boolean;
}) {
  // 需填理由时展开输入框；null 表示未处于处置中
  const [pending, setPending] = useState<ActionItemStatus | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 旧报告的改进项 id 是报告内临时编号（AI-xxxxxxxx），不是数据库主键 → 不可寻址
  const addressable = /^\d+$/.test(item.id);

  async function submit(next: ActionItemStatus, withNote: string) {
    if (disabled || busy) return;
    setBusy(true);
    setError(null);
    try {
      await updateActionItemStatus(item, next, withNote);
      setPending(null);
      setNote("");
      onDisposed();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  function click(next: ActionItemStatus) {
    if (NOTE_REQUIRED.includes(next)) {
      setPending(next);
      return;
    }
    void submit(next, "");
  }

  if (!addressable) {
    return (
      <div className="mt-1 text-[11px] text-zinc-600 dark:text-zinc-400">
        该改进项来自旧版报告，无数据库主键，需重新生成报告后方可处置
      </div>
    );
  }

  return (
    <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
      <span className={`text-[11px] ${STATUS_CLS[status] ?? "text-zinc-600 dark:text-zinc-400"}`}>
        {STATUS_LABEL[status] ?? status}
      </span>
      {status !== "confirmed" && (
        <button
          onClick={() => click("confirmed")}
          disabled={busy || disabled}
          className="rounded border border-zinc-300 px-1.5 py-0.5 text-[11px] text-zinc-600 dark:text-zinc-400 transition-colors hover:border-sky-500 hover:text-sky-400 disabled:opacity-40 dark:border-zinc-700"
        >
          确认
        </button>
      )}
      {status !== "applied" && (
        <button
          onClick={() => click("applied")}
          disabled={busy || disabled}
          className="rounded border border-zinc-300 px-1.5 py-0.5 text-[11px] text-zinc-600 dark:text-zinc-400 transition-colors hover:border-emerald-500 hover:text-emerald-400 disabled:opacity-40 dark:border-zinc-700"
        >
          已实施
        </button>
      )}
      <button
        onClick={() => click("rejected")}
        disabled={busy || disabled}
        className="rounded border border-zinc-300 px-1.5 py-0.5 text-[11px] text-zinc-600 dark:text-zinc-400 transition-colors hover:border-zinc-500 disabled:opacity-40 dark:border-zinc-700"
      >
        驳回
      </button>
      <button
        onClick={() => click("reverted")}
        disabled={busy || disabled}
        className="rounded border border-zinc-300 px-1.5 py-0.5 text-[11px] text-zinc-600 dark:text-zinc-400 transition-colors hover:border-amber-500 hover:text-amber-400 disabled:opacity-40 dark:border-zinc-700"
      >
        回退
      </button>
      {status !== "pending" && (
        <button
          onClick={() => void submit("pending", "")}
          disabled={busy || disabled}
          className="text-[11px] text-zinc-600 dark:text-zinc-400 transition-colors hover:text-zinc-900 disabled:opacity-40 dark:hover:text-zinc-100"
        >
          撤销处置
        </button>
      )}

      {pending !== null && (
        <div className="mt-1 w-full space-y-1">
          <input
            autoFocus
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder={pending === "applied" ? "实施与验证证据：PR、提交或批准记录（必填）" : pending === "rejected" ? "驳回理由（必填）" : "回退理由（必填）"}
            className="w-full rounded border border-zinc-300 bg-transparent px-2 py-1 text-[11px] text-zinc-900 dark:text-zinc-200 outline-none placeholder:text-zinc-600 focus:border-sky-500 dark:border-zinc-700"
          />
          <div className="flex items-center gap-2">
            <button
              onClick={() => void submit(pending, note)}
              disabled={busy || disabled || !note.trim()}
              className="rounded border border-sky-600 px-2 py-0.5 text-[11px] text-sky-700 dark:text-sky-400 disabled:opacity-40"
            >
              提交
            </button>
            <button
              onClick={() => {
                setPending(null);
                setNote("");
                setError(null);
              }}
              className="text-[11px] text-zinc-600 dark:text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-300"
            >
              取消
            </button>
          </div>
        </div>
      )}
      {error && <div className="w-full text-[11px] text-red-700 dark:text-red-400">处置失败：{error}</div>}
    </div>
  );
}

function ReportDetail({
  report,
  onDisposed,
  allowDispose,
  readError = false,
}: {
  allowDispose: boolean;
  readError?: boolean;
  report: ReviewReportDetail;
  onDisposed: () => void;
}) {
  return (
    <div className="review-report space-y-3 px-4 py-3 text-sm">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-zinc-600 dark:text-zinc-400">
        <span className="font-mono">{report.review_id}</span>
        <span>方法论 {report.methodology_version}</span>
        <span>
          模型 {report.model.actual ?? "--"}
          {report.model.degraded && <span className="text-amber-800 dark:text-amber-400">（降级：{report.model.reason ?? "未知"}）</span>}
        </span>
      </div>

      <NarrativeText text={report.summary} label="复盘摘要原文" identity={report.review_id}/>
      {report.meta_insights.length > 0 && <section aria-label="复盘观察与建议" className="space-y-2">
        {report.meta_insights.map((insight, i) => <div key={i} className="border-b border-zinc-300 pb-3 last:border-0 dark:border-zinc-700">
          <ExplanationMap label={insight.dimension} identity={`${report.review_id}:insight:${i}`} steps={[
            {id:"observation",label:"报告观察",icon:"source",summary:readingExcerpt(insight.observation, 84) || "本报告未列观察",content:<NarrativeText text={insight.observation} label="观察原文"/>},
            {id:"evidence",label:"依据与有效性",icon:"response",summary:`报告有效性：${insight.effectiveness || "未记录"}；原依据摘录：${readingExcerpt(insight.evidence, 64) || "未记录"}`,content:<ConditionList label="报告已列依据" items={[`依据：${insight.evidence}`,`有效性：${insight.effectiveness}`]}/>},
            {id:"suggestion",label:"待核建议",icon:"risk",summary:`原建议摘录：${readingExcerpt(insight.suggestion, 84) || "未记录"}`,content:<NarrativeText text={insight.suggestion} label="建议原文"/>},
          ]}/>
        </div>)}
      </section>}

      {report.dimensions.map((d) => (
        <div key={d.key} className="rounded-lg border border-zinc-200 px-3 py-2 dark:border-zinc-800">
          <div className="text-xs font-medium text-zinc-900 dark:text-zinc-100">
            {d.title}
            <span className="ml-2 font-normal text-zinc-600 dark:text-zinc-400">{d.status}</span>
          </div>
          <ExplanationMap label="维度依据" identity={`${report.review_id}:${d.key}`} steps={[
            {id:"findings",label:"报告观察",icon:"source",summary:d.findings.length ? <span className="block"><span className="block">{readingExcerpt(d.findings[0], 84)}</span>{d.findings.length > 1 && <span className="block">共 {d.findings.length} 条原观察，完整记录见节点</span>}</span> : "本报告未列观察，不补填事实",content:d.findings.length ? <ConditionList label="原始观察记录" items={d.findings}/> : <p>本报告未列观察，不补填事实。</p>},
            {id:"judgements",label:"报告判断",icon:"check",summary:d.judgements.length ? `原判断摘录：${readingExcerpt(d.judgements[0], 84)}` : "本报告未列判断",content:d.judgements.length ? <ConditionList label="原始判断" items={d.judgements}/> : <p>本报告未列判断。</p>},
            {id:"gaps",label:"已列缺口",icon:"risk",tone:"warning",summary:d.gaps.length ? <span className="block space-y-1">{d.gaps.map((gap, index)=><span key={index} className="block">{gapText(gap)}</span>)}</span> : "本报告未列缺口，不等于已完整验证",content:d.gaps.length ? <ConditionList label="缺口与限制" items={d.gaps.map(gapText)} tone="warning"/> : <p>本报告未列缺口，不等于已完整验证。</p>},
          ]} footer="报告字段分类不代表独立验证，判断仍需核对来源。"/>
        </div>
      ))}

      {report.action_items.length > 0 && (
        <div>
          <div className="mb-1 text-xs font-medium text-zinc-900 dark:text-zinc-100">
            改进项
            <span className="ml-2 font-normal text-zinc-600 dark:text-zinc-400">管理处置记录，不代表策略效果</span>
          </div>
          <div className="space-y-1.5">
            {report.action_items.map((a) => (
              <div key={JSON.stringify([report.trade_date, a.id, a.category, a.title])} className="rounded-lg border border-zinc-200 px-3 py-2 dark:border-zinc-800">
                <div className="flex items-baseline gap-2 text-xs">
                  <span className={`font-mono font-semibold ${PRIORITY_CLS[a.priority] ?? "text-zinc-600 dark:text-zinc-400"}`}>{a.priority}</span>
                  <span className="text-zinc-900 dark:text-zinc-100">{a.title}</span>
                  <span className="text-zinc-600 dark:text-zinc-400">[{CATEGORY_LABEL[a.category] ?? a.category}]</span>
                </div>
                <NarrativeText text={a.expected_impact} label="预期影响原文"/>
                {a.proposed_change && (
                  <div className="mt-0.5 text-[11px] text-zinc-600 dark:text-zinc-400">
                    建议：<NarrativeText text={a.proposed_change} label="拟议变更原文"/>
                    {a.target && <span className="ml-1 text-zinc-600 dark:text-zinc-400">（{a.target}）</span>}
                  </div>
                )}
                {allowDispose ? <ActionItemDispose
                  item={{ id: a.id, trade_date: report.trade_date, category: a.category, title: a.title }}
                  status={a.status ?? "pending"}
                  onDisposed={onDisposed}
                  disabled={readError}
                /> : <p className="mt-1 text-xs text-zinc-600 dark:text-zinc-400">{STATUS_LABEL[a.status ?? "pending"] ?? a.status} · 管理处置在系统维护中进行</p>}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

/**
 * @param focusDate 深链日期（/research?tab=review&date=YYYY-MM-DD，助手一键跳转用）。
 *   命中报告列表则展开它；列表里没有也照样尝试拉一次（可能未生成 → detail 为 null，
 *   此时明确显示未生成，不回退其它日期）。
 */
export function ReviewTab({ focusDate, allowDispose = false }: { focusDate?: string; allowDispose?: boolean } = {}) {
  const requestedDate = focusDate?.replaceAll("-", "");
  const list = useDispositionResource(getReviewReports, requestedDate);
  const effectiveness = useResource(getReviewEffectiveness, { enabled: allowDispose, intervalMs: null, marketHours: false });
  const reports = list.data ?? null;
  const effect = effectiveness.data ?? null;
  const error = list.error instanceof Error ? list.error.message : list.error ? "读取失败" : null;
  // undefined follows the requested date/latest report; null explicitly closes the detail.
  const [selectedDate, setSelectedDate] = useState<string | null | undefined>();
  const openDate = selectedDate === undefined ? requestedDate ?? reports?.[0]?.trade_date ?? null : selectedDate;
  const detailResource = useDispositionResource(async () => {
    try { return await getReviewReport(openDate!); } catch (error) {
      if (error instanceof ApiError && error.status === 404) return null;
      throw error;
    }
  }, openDate, !!openDate);
  const detail = detailResource.data ?? null;
  const toggle = useCallback((date: string) => setSelectedDate(openDate === date ? null : date), [openDate]);
  const reloadAfterDispose = useCallback(() => {
    list.refresh(); effectiveness.refresh(); detailResource.refresh();
  }, [list, effectiveness, detailResource]);

  if (error && reports === null) {
    return (
      <Panel title="复盘报告">
        <p className="px-4 py-8 text-center text-sm text-amber-800 dark:text-amber-500">复盘报告加载失败：{error}</p><button onClick={list.refresh} className="px-4 py-2">重试读取</button>
      </Panel>
    );
  }
  if (reports === null) {
    return (
      <Panel title="复盘报告">
        <p className="px-4 py-8 text-center text-sm text-zinc-600 dark:text-zinc-400">加载中…</p>
      </Panel>
    );
  }

  return (
    <div className="review-workspace">
      <p className="review-reader-hint">左右滑动查看报告、正文与处置</p>
      {error && <p role="alert" className="px-4 py-2 text-xs text-amber-800 dark:text-amber-300">报告列表刷新失败，保留上次读取：{error} <button onClick={list.refresh}>重试列表</button></p>}
    <div className={`review-layout${allowDispose ? " review-layout-with-disposition" : ""}`} role="region" aria-label="报告索引、正文与处置阅读区" tabIndex={0}>
      <Panel title="复盘报告（冻结版本）" className="review-index" bodyClassName="review-index-scroll overflow-auto">
        {requestedDate && !reports.some(r => r.trade_date === requestedDate) && <div className="px-4 py-3 text-sm"><button aria-pressed={openDate === requestedDate} onClick={() => setSelectedDate(requestedDate)}>{requestedDate} 指定报告</button></div>}
        {reports.length === 0 ? (
          <p className="px-4 py-8 text-sm text-zinc-600 dark:text-zinc-400">
            暂无复盘报告。后端每日盘后自动生成（未生成或调度未运行，可在系统维护核对）。
          </p>
        ) : (
          <div className="divide-y divide-zinc-100 dark:divide-zinc-800/60">
            {reports.map((r) => (
              <button key={r.review_id}
                onClick={() => toggle(r.trade_date)}
                aria-pressed={openDate === r.trade_date}
                className="review-record-trigger w-full px-4 py-3 text-left transition-colors hover:bg-zinc-100/60 dark:hover:bg-zinc-800/40"
              >
                <div className="review-record-heading text-sm">
                  <span className="font-mono text-xs text-zinc-600 dark:text-zinc-400">{r.trade_date}</span>
                  <span className="text-zinc-900 dark:text-zinc-100">{r.summary}</span>
                </div>
                <div className="mt-1 text-xs text-zinc-600 dark:text-zinc-400">
                  缺口 {r.gap_count} · 行动项 {r.action_item_count}
                  {r.model_degraded && <span className="ml-2 text-amber-800 dark:text-amber-400">模型降级</span>}
                </div>
              </button>
            ))}
          </div>
        )}
      </Panel>

      <Panel title={openDate ? `${openDate} 复盘正文` : "复盘正文"} className="review-detail" bodyClassName="review-detail-scroll overflow-auto">
        {allowDispose && detail && (list.inFlight || detailResource.inFlight) && <p role="status" className="px-4 py-2 text-xs text-zinc-600 dark:text-zinc-400">正在核对当前处置状态，暂不可操作。</p>}
        {!!detailResource.error && detail && <p role="alert" className="px-4 py-2 text-xs text-amber-800 dark:text-amber-300">正文刷新失败，保留上次报告；当前处置状态待确认。<button onClick={detailResource.refresh}>重试正文</button></p>}
        {openDate ? detail ? <ReportDetail report={detail} allowDispose={allowDispose} readError={list.reading || detailResource.reading || !!list.error || !!detailResource.error} onDisposed={reloadAfterDispose} /> : (
          <div className="px-4 py-4 text-sm text-zinc-600 dark:text-zinc-400">
            {detailResource.error ? <><p role="alert">{openDate} 报告读取失败。</p><button onClick={detailResource.refresh}>重试读取</button></> : detailResource.pending ? <p role="status">详情加载中…</p> : <p role="status">{requestedDate === openDate && !reports.some(r => r.trade_date === openDate) ? "指定日期尚无报告" : `${openDate} 尚无报告，不回退成其它日期。`}</p>}
          </div>
        ) : <p className="px-4 py-4 text-sm text-zinc-600 dark:text-zinc-400">从报告列表选择日期，查看冻结记录与改进项。</p>}
      </Panel>

      {allowDispose && <Panel title="改进项处置统计（非交易效果）" className="review-disposition" bodyClassName="review-disposition-scroll overflow-auto">
        {!!effectiveness.error && effect && <p role="alert" className="px-4 py-2 text-xs text-amber-800 dark:text-amber-300">处置统计刷新失败，保留上次统计。<button onClick={effectiveness.refresh}>重试统计</button></p>}
        {effect === null ? (
          <p className="px-4 py-8 text-center text-sm text-zinc-600 dark:text-zinc-400">{effectiveness.error ? "处置统计读取失败，不能判断当前状态。" : effectiveness.pending ? "读取处置统计…" : "暂无处置统计。"}</p>
        ) : (
          <table className="data-table w-full text-sm">
            <thead>
              <tr className="border-b border-zinc-200 text-xs text-zinc-600 dark:text-zinc-400 dark:border-zinc-800">
                <th className="px-4 py-2 text-left font-normal">类别</th>
                <th className="px-2 py-2 text-right font-normal">总数</th>
                <th className="px-2 py-2 text-right font-normal">已采纳</th>
                <th className="px-2 py-2 text-right font-normal">回退</th>
                <th className="px-2 py-2 text-right font-normal">驳回</th>
                <th className="px-4 py-2 text-right font-normal">采纳率</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(effect.by_category).map(([cat, v]) => (
                <tr key={cat} className="border-b border-zinc-100 last:border-0 dark:border-zinc-800/60">
                  <td className="px-4 py-2">{CATEGORY_LABEL[cat] ?? cat}</td>
                  <td className="px-2 py-2 text-right font-mono">{v.total}</td>
                  <td className="px-2 py-2 text-right font-mono text-up-ink dark:text-up">{v.confirmed}</td>
                  <td className="px-2 py-2 text-right font-mono text-down-ink dark:text-down">{v.reverted}</td>
                  <td className="px-2 py-2 text-right font-mono text-zinc-600 dark:text-zinc-400">{v.rejected}</td>
                  <td className="px-4 py-2 text-right font-mono">{v.adoption_rate != null ? `${Math.round(v.adoption_rate * 100)}%` : "--"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>}
    </div>
    </div>
  );
}
