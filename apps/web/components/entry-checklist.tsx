"use client";

import { useEffect, useState } from "react";

import { getEntryChecklist, type EntryChecklist as EntryChecklistData } from "@/lib/api";

/**
 * 介入条件清单（P1-13）：`dragon_service.entry_checklist` 的界面出口。
 *
 * 回答「等确认往往已涨一轮，追进去又被套」——不给"明天买 X"，只给必须同时满足的
 * 信号清单（市场层 → 题材层 → 个股层）+ 回避项 + 失效条件 + 时间窗口。
 *
 * 数据来源 `/api/market/entry-checklist`：后端复用题材看板与情绪判定的 60s 缓存，
 * 组件按需拉取（展开才请求），不参与页面主轮询。
 *
 * 红线 3：全部为条件陈述，不构成买卖建议。
 */
export function EntryChecklist({ symbol }: { symbol: string }) {
  // 三态：undefined=尚未拉到（加载中）/ null=拉取失败 / 有值=渲染
  const [data, setData] = useState<EntryChecklistData | null | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);

  // 切换标的 → 当帧回到「加载中」，不残留上一只的清单（渲染期 adjust-state；
  // effect 体内同步 setState 会多一帧旧数据并触发 react-hooks/set-state-in-effect）
  const [prevSymbol, setPrevSymbol] = useState(symbol);
  if (symbol !== prevSymbol) {
    setPrevSymbol(symbol);
    setData(undefined);
    setError(null);
  }

  useEffect(() => {
    let alive = true;
    getEntryChecklist(symbol)
      .then((d) => {
        if (alive) setData(d);
      })
      .catch((e: unknown) => {
        if (!alive) return;
        setData(null);
        setError(e instanceof Error ? e.message : "加载失败");
      });
    return () => {
      alive = false;
    };
  }, [symbol]);

  if (data === undefined) {
    return <p className="text-[11px] text-zinc-600 dark:text-zinc-400">介入条件加载中…</p>;
  }
  if (error) {
    return <p className="text-[11px] text-amber-800 dark:text-amber-400">介入条件不可用：{error}</p>;
  }
  if (!data) return null;

  return (
    <div className="space-y-2 text-[11px] leading-relaxed" data-testid="entry-checklist">
      {/* 三层环境：市场 / 题材 */}
      <div className="flex flex-wrap gap-1.5">
        <LayerBadge
          label={`市场 ${data.market_layer.phase ?? "未判定"}`}
          blocked={data.market_layer.blocked}
        />
        <LayerBadge
          label={`题材 ${data.theme_layer.stage ?? "未判定"}`}
          blocked={data.theme_layer.blocked}
        />
        {data.role && (
          <span className="rounded bg-zinc-100 px-1.5 py-px text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300">
            {data.role}
            {data.boards ? ` · ${data.boards}板` : ""}
          </span>
        )}
        {/* 维度前缀必须是「龙头评级」。此处曾误写成直接量 `龙头相`（即 DRAGON_GRADES[0]），
            而后端 `dragon_grade` 的取值本身就是「龙头相 / 强势候选 / 观察 / 杂毛·回避」，
            于是渲染成「龙头相 龙头相」的语义重复。前缀只写维度，取值一律来自后端。 */}
        {data.dragon_grade && (
          <span
            className="rounded bg-indigo-500/10 px-1.5 py-px text-indigo-700 dark:text-indigo-300"
            title="龙头评级：≥12 龙头相 / ≥8 强势候选 / ≥4 观察 / <4 杂毛·回避"
          >
            龙头评级 {data.dragon_grade}
          </span>
        )}
      </div>

      <p className="text-zinc-600 dark:text-zinc-400">
        {data.market_layer.note}
        {data.theme_layer.blocked ? ` ｜${data.theme_layer.note}` : ""}
      </p>

      {!data.found && (
        <p className="rounded bg-amber-500/10 px-2 py-1 text-amber-800 dark:text-amber-300">
          该标的当日不在题材梯队（未涨停或未归入题材）→ 个股层封单质量/角色/题材阶段未判定。
        </p>
      )}

      <Section title="需同时满足的信号" tone="ok" items={data.conditions} ordered />
      {data.avoid.length > 0 && <Section title="回避项" tone="warn" items={data.avoid} />}
      <Section title="失效条件（出现即推翻判断）" tone="danger" items={data.invalidation} />

      <p className="text-zinc-600 dark:text-zinc-400">
        <span className="text-zinc-600 dark:text-zinc-400">时间窗口：</span>
        {data.timing}
      </p>

      {data.missing.length > 0 && (
        <p className="text-zinc-600 dark:text-zinc-400">
          未取到：{data.missing.join("、")}（按「未判定」呈现，不做中性假设）
        </p>
      )}

      <p className="text-[10px] text-zinc-600 dark:text-zinc-400">{data.note}</p>
    </div>
  );
}

const TONE: Record<string, string> = {
  ok: "text-emerald-700 dark:text-emerald-400",
  warn: "text-amber-800 dark:text-amber-400",
  danger: "text-rose-700 dark:text-rose-400",
};

function Section({
  title,
  items,
  tone,
  ordered = false,
}: {
  title: string;
  items: string[];
  tone: keyof typeof TONE;
  ordered?: boolean;
}) {
  if (items.length === 0) return null;
  const List = ordered ? "ol" : "ul";
  return (
    <div>
      <div className={`font-medium ${TONE[tone]}`}>{title}</div>
      <List className={`ml-4 space-y-0.5 text-zinc-600 dark:text-zinc-300 ${ordered ? "list-decimal" : "list-disc"}`}>
        {items.map((t) => (
          <li key={t}>{t}</li>
        ))}
      </List>
    </div>
  );
}

function LayerBadge({ label, blocked }: { label: string; blocked: boolean }) {
  return (
    <span
      className={
        blocked
          ? "rounded bg-rose-500/10 px-1.5 py-px text-rose-700 dark:text-rose-300"
          : "rounded bg-emerald-500/10 px-1.5 py-px text-emerald-700 dark:text-emerald-300"
      }
      title={blocked ? "该层环境不允许介入" : "该层环境未拦阻"}
    >
      {label}
      {blocked ? " · 拦阻" : ""}
    </span>
  );
}
