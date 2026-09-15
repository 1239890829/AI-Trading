"use client";

/**
 * 盘后增强（P1-5/P1-6，2026-09-09）：接力质量排序 + 潜伏观察池。
 * 折叠展示，默认收起（辅助信息，不占猎场常驻注意力）；展开时拉取。
 * 定位纪律：两表都是「顺序/观察参考」，非买卖信号——口径说明常驻小字。
 */
import { useEffect, useState } from "react";
import Link from "next/link";

import { getLurkPool, getRelayRank, type LurkPoolPayload, type RelayRankItem } from "@/lib/api";
import { workbenchUrl } from "@/lib/routing";
import { symbolDetailClick, useSymbolDetail } from "@/components/detail/symbol-detail-context";

type State<T> =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "error"; msg: string }
  | { status: "ready"; data: T };

/** 接力/潜伏表里的个股：点击**就地弹窗**看详情（2026-09-15 详情弹窗化）。 */
function SymbolLink({ symbol, name }: { symbol: string; name?: string }) {
  const { open } = useSymbolDetail();
  return (
    <Link
      href={workbenchUrl(symbol)}
      onClick={symbolDetailClick(open, { symbol })}
      className="font-mono text-zinc-700 hover:text-sky-600 dark:text-zinc-300 dark:hover:text-sky-400"
      title={`${symbol}${name ? ` ${name}` : ""} · 查看详情`}
    >
      {symbol}
      {name && <span className="ml-1 font-sans">{name}</span>}
    </Link>
  );
}

function SectionLabel({ text, tone }: { text: string; tone: "sky" | "amber" | "teal" }) {
  const c =
    tone === "sky"
      ? "border-sky-500/40 bg-sky-500/10 text-sky-700 dark:text-sky-300"
      : tone === "teal"
        ? "border-teal-500/40 bg-teal-500/10 text-teal-700 dark:text-teal-300"
        : "border-amber-500/40 bg-amber-500/10 text-amber-800 dark:text-amber-300";
  return <span className={`shrink-0 rounded border px-1.5 py-0.5 text-[10px] ${c}`}>{text}</span>;
}

function RelayTable({ s }: { s: State<{ trade_date: string; items: RelayRankItem[] }> }) {
  if (s.status === "idle" || s.status === "loading") return <p className="py-2 text-xs text-zinc-600 dark:text-zinc-400">次日接力参考加载中…</p>;
  if (s.status === "error") return <p className="py-2 text-xs text-amber-800 dark:text-amber-600">{s.msg}</p>;
  if (s.status === "ready" && s.data.items.length === 0)
    return <p className="py-2 text-xs text-zinc-600 dark:text-zinc-400">今日涨停池为空或暂无排序结果。</p>;
  const { trade_date, items } = s.data;
  return (
    <div>
      <div className="mb-1 text-[10px] text-zinc-600 dark:text-zinc-400">交易日 {trade_date} · kmid2 封板实度 × max20 距新高（P1-3 实证正 IC）</div>
      <ul className="space-y-1">
        {items.slice(0, 10).map((it) => (
          <li key={it.symbol} className="flex items-baseline gap-2 text-xs">
            <span className="shrink-0 text-zinc-600 dark:text-zinc-400">{it.boards}板</span>
            <SymbolLink symbol={it.symbol} name={it.name} />
            {it.reason && <span className="truncate text-[11px] text-zinc-600 dark:text-zinc-400">{it.reason}</span>}
            <span className="ml-auto shrink-0 font-mono text-[10px] text-zinc-600 dark:text-zinc-400">
              {it.max20 != null ? `${(it.max20 * 100).toFixed(0)}%距高` : "--"}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function LurkTable({ s, date }: { s: State<LurkPoolPayload>; date: (ms: number) => string }) {
  if (s.status === "idle" || s.status === "loading") return <p className="py-2 text-xs text-zinc-600 dark:text-zinc-400">潜伏观察池加载中…</p>;
  if (s.status === "error") return <p className="py-2 text-xs text-amber-800 dark:text-amber-600">{s.msg}</p>;
  const { items, as_of, stale_days, stale, stale_note } = s.data;
  return (
    <div>
      {/* 陈旧披露（2026-09-11）：池按**库内最新 K 线**判定，停更时看着正常却是旧数据。
          新鲜时只标「数据截至」；滞后超阈值才显式告警——不降级、不隐藏，只摊开口径。 */}
      <div className="mb-1 text-[10px] text-zinc-600 dark:text-zinc-400">
        确认日=缩量回踩不破位当日（最近 {items.length} 只） · 数据截至 {as_of}
        {!stale && stale_days > 0 && `（滞后 ${stale_days} 个交易日，阈值内属正常）`}
      </div>
      {stale && (
        <p className="mb-1 rounded border border-amber-500/40 bg-amber-500/10 px-1.5 py-1 text-[10px] leading-relaxed text-amber-800 dark:text-amber-300">
          {stale_note}
        </p>
      )}
      {items.length === 0 ? (
        /* 空池也必须带披露：marketdb 停更时「今日无票」本身可能就是旧数据的结论（2026-09-11 修复：
           原实现在此**提前 return**，把上面的陈旧披露整段吞掉 ⇒ 恰是披露要消除的「静默」）。 */
        <p className="py-2 text-xs text-zinc-600 dark:text-zinc-400">当前无「潜伏+试盘回踩确认」观察票。</p>
      ) : (
        <ul className="space-y-1">
          {items.slice(0, 10).map((it) => (
            <li key={it.symbol} className="flex items-baseline gap-2 text-xs">
              <SymbolLink symbol={it.symbol} />
              <span className="ml-auto shrink-0 text-[10px] text-zinc-600 dark:text-zinc-400">确认 {date(it.confirm_ms)}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function PostMarketEnhance() {
  const [relay, setRelay] = useState<State<{ trade_date: string; items: RelayRankItem[] }>>({ status: "idle" });
  const [lurk, setLurk] = useState<State<LurkPoolPayload>>({ status: "idle" });
  const [open, setOpen] = useState(false);

  // 展开即回到「加载中」（渲染期 adjust-state；原为 effect 内同步 setState，P1-27）
  const [prevOpen, setPrevOpen] = useState(false);
  if (prevOpen !== open) {
    setPrevOpen(open);
    if (open) {
      setRelay({ status: "loading" });
      setLurk({ status: "loading" });
    }
  }

  useEffect(() => {
    if (!open) return;
    let alive = true;
    getRelayRank()
      .then((d) => alive && setRelay({ status: "ready", data: d }))
      .catch((e: Error) => alive && setRelay({ status: "error", msg: e.message }));
    getLurkPool()
      .then((d) => alive && setLurk({ status: "ready", data: d }))
      .catch((e: Error) => alive && setLurk({ status: "error", msg: e.message }));
    return () => {
      alive = false;
    };
  }, [open]);

  const dateText = (ms: number) => {
    const d = new Date(ms);
    return `${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  };

  return (
    <details
      open={open}
      onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}
      className="rounded-xl border border-zinc-200 px-3 py-2 dark:border-zinc-800"
    >
      <summary className="cursor-pointer select-none text-xs font-medium text-zinc-600 dark:text-zinc-400">
        盘后增强（次日接力参考 · 潜伏观察池 —— P1 实证因子）
      </summary>
      <div className="mt-3 grid grid-cols-1 gap-4 lg:grid-cols-2">
        <section>
          <div className="flex items-center gap-1.5">
            <SectionLabel text="次日接力参考" tone="sky" />
            <span className="text-[10px] text-zinc-600 dark:text-zinc-400">今日涨停池 · 按实证接力质量排序（仅供参考，非买卖信号）</span>
          </div>
          <div className="mt-1.5">
            <RelayTable s={relay} />
          </div>
        </section>
        <section>
          <div className="flex items-center gap-1.5">
            <SectionLabel text="潜伏观察" tone="teal" />
            <span className="text-[10px] text-zinc-600 dark:text-zinc-400">缩量横盘+试盘+回踩不破位 · 中线观察（20 日涨停率 1.67× 实证）</span>
          </div>
          <div className="mt-1.5">
            <LurkTable s={lurk} date={dateText} />
          </div>
        </section>
      </div>
    </details>
  );
}
