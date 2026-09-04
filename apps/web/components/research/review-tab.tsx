"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Panel } from "@/components/panel";
import { workbenchUrlWithBack } from "@/lib/routing";
import {
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
 * 采纳率是方法论自我校准的度量。
 */

const PRIORITY_CLS: Record<string, string> = {
  P0: "text-red-400",
  P1: "text-amber-400",
  P2: "text-zinc-400",
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
  confirmed: "已确认",
  applied: "已实施",
  rejected: "已驳回",
  reverted: "已回退",
};

const STATUS_CLS: Record<string, string> = {
  pending: "text-zinc-400",
  confirmed: "text-sky-400",
  applied: "text-emerald-400",
  rejected: "text-zinc-500 line-through",
  reverted: "text-amber-400",
};

/** 需要填写理由才能提交的状态——后端强制校验，前端同步提示，避免点了才报错。 */
const NOTE_REQUIRED: ActionItemStatus[] = ["rejected", "reverted"];

/** gaps 元素可能是字符串，也可能是结构化对象（{field,reason,impact,severity}）——统一成可读文本。 */
function gapText(g: unknown): string {
  if (typeof g === "string") return g;
  if (g && typeof g === "object") {
    const o = g as Record<string, unknown>;
    const parts = [
      o.field ? String(o.field) : null,
      o.reason ? String(o.reason) : null,
      o.impact ? `影响 ${o.impact}` : null,
    ].filter(Boolean);
    return parts.join("：") || JSON.stringify(o);
  }
  return String(g);
}

/**
 * 报告文本内嵌 6 位代码 → 详情链接（联动切片 F P2）。
 * 复盘判据/缺口文本里提到的标的（如「600519 冲高回落」）此前无法跳转查看；
 * 独立 6 位数字在本系统语境下几乎恒为股票代码，误链风险可接受。
 */
function LinkedSymbols({ text }: { text: string }) {
  const parts = text.split(/(\b\d{6}\b)/g);
  if (parts.length === 1) return <>{text}</>;
  return (
    <>
      {parts.map((p, i) =>
        /^\d{6}$/.test(p) ? (
          <Link
            key={i}
            href={workbenchUrlWithBack(p)}
            title="查看标的详情"
            className="font-mono text-sky-400 hover:underline"
          >
            {p}
          </Link>
        ) : (
          <span key={i}>{p}</span>
        ),
      )}
    </>
  );
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
}: {
  item: ActionItemRef;
  status: string;
  onDisposed: () => void;
}) {
  // 需填理由时展开输入框；null 表示未处于处置中
  const [pending, setPending] = useState<ActionItemStatus | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 旧报告的改进项 id 是报告内临时编号（AI-xxxxxxxx），不是数据库主键 → 不可寻址
  const addressable = /^\d+$/.test(item.id);

  async function submit(next: ActionItemStatus, withNote: string) {
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
      <div className="mt-1 text-[11px] text-zinc-500">
        该改进项来自旧版报告，无数据库主键，需重新生成报告后方可处置
      </div>
    );
  }

  return (
    <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
      <span className={`text-[11px] ${STATUS_CLS[status] ?? "text-zinc-400"}`}>
        {STATUS_LABEL[status] ?? status}
      </span>
      {status !== "confirmed" && (
        <button
          onClick={() => click("confirmed")}
          disabled={busy}
          className="rounded border border-zinc-300 px-1.5 py-0.5 text-[11px] text-zinc-400 transition-colors hover:border-sky-500 hover:text-sky-400 disabled:opacity-40 dark:border-zinc-700"
        >
          确认
        </button>
      )}
      {status !== "applied" && (
        <button
          onClick={() => click("applied")}
          disabled={busy}
          className="rounded border border-zinc-300 px-1.5 py-0.5 text-[11px] text-zinc-400 transition-colors hover:border-emerald-500 hover:text-emerald-400 disabled:opacity-40 dark:border-zinc-700"
        >
          已实施
        </button>
      )}
      <button
        onClick={() => click("rejected")}
        disabled={busy}
        className="rounded border border-zinc-300 px-1.5 py-0.5 text-[11px] text-zinc-400 transition-colors hover:border-zinc-500 disabled:opacity-40 dark:border-zinc-700"
      >
        驳回
      </button>
      <button
        onClick={() => click("reverted")}
        disabled={busy}
        className="rounded border border-zinc-300 px-1.5 py-0.5 text-[11px] text-zinc-400 transition-colors hover:border-amber-500 hover:text-amber-400 disabled:opacity-40 dark:border-zinc-700"
      >
        回退
      </button>
      {status !== "pending" && (
        <button
          onClick={() => void submit("pending", "")}
          disabled={busy}
          className="text-[11px] text-zinc-500 underline-offset-2 hover:underline disabled:opacity-40"
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
            placeholder={pending === "rejected" ? "驳回理由（必填）" : "回退理由（必填）"}
            className="w-full rounded border border-zinc-300 bg-transparent px-2 py-1 text-[11px] text-zinc-200 outline-none placeholder:text-zinc-600 focus:border-sky-500 dark:border-zinc-700"
          />
          <div className="flex items-center gap-2">
            <button
              onClick={() => void submit(pending, note)}
              disabled={busy || !note.trim()}
              className="rounded border border-sky-600 px-2 py-0.5 text-[11px] text-sky-400 disabled:opacity-40"
            >
              提交
            </button>
            <button
              onClick={() => {
                setPending(null);
                setNote("");
                setError(null);
              }}
              className="text-[11px] text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-300"
            >
              取消
            </button>
          </div>
        </div>
      )}
      {error && <div className="w-full text-[11px] text-red-400">处置失败：{error}</div>}
    </div>
  );
}

function ReportDetail({
  report,
  onDisposed,
}: {
  report: ReviewReportDetail;
  onDisposed: () => void;
}) {
  return (
    <div className="space-y-3 px-4 py-3 text-sm">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-zinc-400">
        <span className="font-mono">{report.review_id}</span>
        <span>方法论 {report.methodology_version}</span>
        <span>
          模型 {report.model.actual ?? "--"}
          {report.model.degraded && <span className="text-amber-400">（降级：{report.model.reason ?? "未知"}）</span>}
        </span>
      </div>

      {report.dimensions.map((d) => (
        <div key={d.key} className="rounded-lg border border-zinc-200 px-3 py-2 dark:border-zinc-800">
          <div className="text-xs font-medium text-zinc-200 dark:text-zinc-100">
            {d.title}
            <span className="ml-2 font-normal text-zinc-400">{d.status}</span>
          </div>
          {/* findings（事实层，2026-09-04 补渲染）：含 picks 维度的失误逐股归因 */}
          {d.findings.length > 0 && (
            <ul className="mt-1 list-disc space-y-0.5 pl-4 text-xs text-zinc-600 dark:text-zinc-300">
              {d.findings.map((f, i) => (
                <li key={i}>
                  <LinkedSymbols text={f} />
                </li>
              ))}
            </ul>
          )}
          {d.judgements.length > 0 && (
            <ul className="mt-1 list-disc space-y-0.5 pl-4 text-xs text-zinc-400">
              {d.judgements.map((j, i) => (
                <li key={i}>
                  <LinkedSymbols text={j} />
                </li>
              ))}
            </ul>
          )}
          {d.gaps.length > 0 && (
            <div className="mt-1 text-xs text-amber-400">
              缺口：
              <ul className="list-disc pl-4">
                {d.gaps.map((g, i) => (
                  <li key={i}>
                    <LinkedSymbols text={gapText(g)} />
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      ))}

      {report.action_items.length > 0 && (
        <div>
          <div className="mb-1 text-xs font-medium text-zinc-200 dark:text-zinc-100">
            改进项
            <span className="ml-2 font-normal text-zinc-500">处置后计入采纳率统计</span>
          </div>
          <div className="space-y-1.5">
            {report.action_items.map((a) => (
              <div key={a.id} className="rounded-lg border border-zinc-200 px-3 py-2 dark:border-zinc-800">
                <div className="flex items-baseline gap-2 text-xs">
                  <span className={`font-mono font-semibold ${PRIORITY_CLS[a.priority] ?? "text-zinc-400"}`}>{a.priority}</span>
                  <span className="text-zinc-200 dark:text-zinc-100">{a.title}</span>
                  <span className="text-zinc-400">[{CATEGORY_LABEL[a.category] ?? a.category}]</span>
                </div>
                <div className="mt-0.5 text-[11px] text-zinc-400">{a.expected_impact}</div>
                {a.proposed_change && (
                  <div className="mt-0.5 text-[11px] text-zinc-500">
                    建议：{a.proposed_change}
                    {a.target && <span className="ml-1 text-zinc-600">（{a.target}）</span>}
                  </div>
                )}
                <ActionItemDispose
                  item={{ id: a.id, trade_date: report.trade_date, category: a.category, title: a.title }}
                  status={a.status ?? "pending"}
                  onDisposed={onDisposed}
                />
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export function ReviewTab() {
  const [reports, setReports] = useState<ReviewReportSummary[] | null>(null);
  const [effect, setEffect] = useState<Awaited<ReturnType<typeof getReviewEffectiveness>> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [openDate, setOpenDate] = useState<string | null>(null);
  const [detail, setDetail] = useState<ReviewReportDetail | null>(null);

  useEffect(() => {
    let alive = true;
    Promise.all([getReviewReports(), getReviewEffectiveness().catch(() => null)])
      .then(async ([r, e]) => {
        if (!alive) return;
        setReports(r);
        setEffect(e);
        // 默认展开最新一份报告并立即拉详情（否则一直停在"加载中"直到手点）
        if (r.length > 0) {
          setOpenDate(r[0].trade_date);
          setDetail(await getReviewReport(r[0].trade_date).catch(() => null));
        }
      })
      .catch((e: Error) => alive && setError(e.message));
    return () => {
      alive = false;
    };
  }, []);

  const toggle = useCallback(
    (date: string) => {
      const next = openDate === date ? null : date;
      setOpenDate(next);
      setDetail(null);
      if (next) {
        getReviewReport(next)
          .then((d) => setDetail(d))
          .catch(() => setDetail(null));
      }
    },
    [openDate],
  );

  /** 处置改进项后刷新三处：报告列表（计数不变但要同步）、当前详情、有效性统计。
   *  只刷新详情会让右侧"采纳率"停留在旧值，看不出处置效果。 */
  const reloadAfterDispose = useCallback(async () => {
    const [r, e] = await Promise.all([
      getReviewReports().catch(() => null),
      getReviewEffectiveness().catch(() => null),
    ]);
    if (r) setReports(r);
    if (e) setEffect(e);
    if (openDate) {
      const d = await getReviewReport(openDate).catch(() => null);
      if (d) setDetail(d);
    }
  }, [openDate]);

  if (error) {
    return (
      <Panel title="复盘报告">
        <p className="px-4 py-8 text-center text-sm text-amber-500">复盘报告加载失败：{error}</p>
      </Panel>
    );
  }
  if (reports === null) {
    return (
      <Panel title="复盘报告">
        <p className="px-4 py-8 text-center text-sm text-zinc-400">加载中…</p>
      </Panel>
    );
  }

  return (
    // minmax(0,…) 而非 auto/1fr：grid 行的 auto 会被长内容无限撑开，把同行面板压扁
    // 并把滚动推到最外层（窄屏下表现为整页滚动、面板内无滚动条）。
    <div className="grid min-h-0 flex-1 grid-rows-[minmax(0,auto)_minmax(0,1fr)] gap-3 overflow-auto lg:grid-cols-2 lg:grid-rows-1">
      <Panel title="复盘报告（盘后自动生成）" className="min-h-[240px]">
        {reports.length === 0 ? (
          <p className="px-4 py-8 text-center text-sm text-zinc-400">
            暂无复盘报告。后端每日盘后自动生成（POST /api/review/run）。
          </p>
        ) : (
          <div className="divide-y divide-zinc-100 dark:divide-zinc-800/60">
            {reports.map((r) => (
              <div key={r.review_id}>
                <button
                  onClick={() => toggle(r.trade_date)}
                  className="w-full px-4 py-2.5 text-left transition-colors hover:bg-zinc-100/60 dark:hover:bg-zinc-800/40"
                >
                  <div className="flex items-baseline gap-2 text-sm">
                    <span className="font-mono text-xs text-zinc-400">{r.trade_date}</span>
                    <span className="text-zinc-200 dark:text-zinc-100">{r.summary}</span>
                  </div>
                  <div className="mt-0.5 text-[11px] text-zinc-400">
                    缺口 {r.gap_count} · 行动项 {r.action_item_count}
                    {r.model_degraded && <span className="ml-2 text-amber-400">模型降级</span>}
                  </div>
                </button>
                {openDate === r.trade_date &&
                  (detail ? (
                    // 展开的详情（元洞察 + 改进项 + 数据缺口）可能很长：限高并在**面板内**
                    // 滚动，避免把外层 grid 行撑高、滚动条跑到页面最底部看不见。
                    // overscroll-contain：滚到边界时不把滚动传导给父容器。
                    <div className="max-h-[55vh] overflow-y-auto overscroll-contain">
                      <ReportDetail report={detail} onDisposed={() => void reloadAfterDispose()} />
                    </div>
                  ) : (
                    <p className="px-4 py-3 text-xs text-zinc-400">详情加载中…</p>
                  ))}
              </div>
            ))}
          </div>
        )}
      </Panel>

      <Panel title="改进项有效性（方法论自校准）" className="min-h-[240px]">
        {effect === null ? (
          <p className="px-4 py-8 text-center text-sm text-zinc-400">有效性统计加载失败或暂无数据</p>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-zinc-200 text-xs text-zinc-400 dark:border-zinc-800">
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
                  <td className="px-2 py-2 text-right font-mono text-up">{v.confirmed}</td>
                  <td className="px-2 py-2 text-right font-mono text-down">{v.reverted}</td>
                  <td className="px-2 py-2 text-right font-mono text-zinc-400">{v.rejected}</td>
                  <td className="px-4 py-2 text-right font-mono">{v.adoption_rate != null ? `${Math.round(v.adoption_rate * 100)}%` : "--"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>
    </div>
  );
}
