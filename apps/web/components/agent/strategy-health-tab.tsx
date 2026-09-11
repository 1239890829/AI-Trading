"use client";

/**
 * 策略健康度 + 登记册面板（#11）。
 *
 * 后端能力早就就绪（P1-37 登记册 / P1-38 策略级监控），但前端此前**没有任何消费方**
 * —— 端点返回 200 且数据正确，界面上却完全看不到。本 tab 就是把这条断掉的链补上。
 *
 * 渲染纪律（三条，违反即误导）：
 * 1. `ok/warning/drift` = **有判定**；`insufficient/thin/no_pipeline/unknown/error` = **判不出**。
 *    后者既不是健康也不是失效，必须视觉上区分，绝不能当成 ok 显示成绿色。
 * 2. `basis` 必须显式标注：`market_neutral`（测 alpha 衰减）与 `absolute`（测策略自身变差）
 *    **不可互相解释**。
 * 3. 核验结论（`verification`）缺失时如实说"尚无核验产物"，不得留白让人以为已验证。
 */
import { useEffect, useState } from "react";

import {
  getStrategyHealth,
  getStrategyRegistry,
  type StrategyHealthItem,
  type StrategyHealthPayload,
  type StrategyRegistryItem,
} from "@/lib/api";

/** 有判定的状态（其余一律视为"判不出"） */
const JUDGED = new Set(["ok", "warning", "drift"]);

const STATUS_META: Record<string, { label: string; cls: string }> = {
  ok: { label: "健康", cls: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300" },
  warning: { label: "关注", cls: "bg-amber-500/15 text-amber-700 dark:text-amber-300" },
  drift: { label: "下漂", cls: "bg-rose-500/15 text-rose-700 dark:text-rose-300" },
  // 判不出（不是 ok 也不是失效）
  insufficient: { label: "样本不足", cls: "bg-zinc-500/15 text-zinc-600 dark:text-zinc-400" },
  thin: { label: "笔数不足", cls: "bg-zinc-500/15 text-zinc-600 dark:text-zinc-400" },
  no_pipeline: { label: "无逐日落库", cls: "bg-zinc-500/15 text-zinc-600 dark:text-zinc-400" },
  unknown: { label: "未知", cls: "bg-zinc-500/15 text-zinc-600 dark:text-zinc-400" },
  error: { label: "读取失败", cls: "bg-rose-500/15 text-rose-700 dark:text-rose-300" },
};

const LIFECYCLE_LABEL: Record<string, string> = {
  active: "现役", observing: "观察", rejected: "已否决",
};

function StatusBadge({ status }: { status?: string }) {
  const s = status || "unknown";
  const meta = STATUS_META[s] ?? { label: s, cls: "bg-zinc-500/15 text-zinc-600 dark:text-zinc-400" };
  const judged = JUDGED.has(s);
  return (
    // data-testid 用于精确断言：**不能靠全文匹配**——caveat 里也含"判不出"字样，
    // 全文断言在徽标不再标注时仍会命中，等于没测（2026-09-11 注入验证抓到）。
    <span
      data-testid={`status-badge-${s}`}
      className={`rounded px-1.5 py-0.5 text-[10px] ${meta.cls}`}
      title={judged ? "有判定" : "样本/能力不足，判不出——既不是健康也不是失效"}
    >
      {meta.label}
      {!judged && "（判不出）"}
    </span>
  );
}

export function StrategyHealthTab() {
  const [items, setItems] = useState<StrategyHealthItem[]>([]);
  const [registry, setRegistry] = useState<StrategyRegistryItem[]>([]);
  const [counts, setCounts] = useState<StrategyHealthPayload["counts"]>(undefined);
  const [caveat, setCaveat] = useState<string>("");
  const [loaded, setLoaded] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);

  // 取数写在 effect **内部**的 async IIFE：项目既有 tab 都这样写——
  // 把 setState 放到 await 之后，避免 `react-hooks/set-state-in-effect`
  // （该规则已由 P1-27 清零，不得重新引入）。
  // 刷新通过 `nonce` 递增重跑；`alive` 防止快速切换时的竞态覆盖。
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const [health, reg] = await Promise.all([getStrategyHealth(), getStrategyRegistry()]);
        if (!alive) return;
        setItems(health.strategies ?? []);
        setCounts(health.counts);
        setCaveat(health.caveat ?? "");
        setRegistry(reg);
        setFailed(null);
      } catch (exc) {
        if (alive) setFailed(exc instanceof Error ? exc.message : "加载失败");
      } finally {
        if (alive) setLoaded(true);
      }
    })();
    return () => {
      alive = false;
    };
  }, [nonce]);

  const refresh = () => setNonce((n) => n + 1);

  // 登记册按 key 索引，取核验结论（S2-11：状态背后的可回查证据）
  const verifyByKey = new Map(registry.map((r) => [r.key, r.verification]));

  return (
    <div className="flex h-full min-h-0 flex-col gap-2 overflow-y-auto">
      <div className="flex flex-wrap items-center gap-2 text-[11px] text-zinc-600 dark:text-zinc-400">
        <span>
          共 {counts?.total ?? items.length} 个策略键 · 可评估 {counts?.evaluable ?? "—"} ·
          需关注 {counts?.attention ?? "—"}
        </span>
        <button
          type="button"
          onClick={refresh}
          className="rounded border border-zinc-200 px-1.5 py-0.5 text-[10px] hover:bg-zinc-50 dark:border-zinc-700 dark:hover:bg-zinc-800"
        >
          刷新
        </button>
      </div>

      {caveat && (
        <p className="rounded border border-zinc-200 px-2 py-1 text-[10px] leading-relaxed text-zinc-600 dark:border-zinc-700 dark:text-zinc-400">
          {caveat}
        </p>
      )}

      {failed && (
        <p className="rounded border border-rose-200 bg-rose-50 px-2 py-1 text-[11px] text-rose-700 dark:border-rose-900 dark:bg-rose-950/40 dark:text-rose-300">
          加载失败：{failed}
        </p>
      )}

      {loaded && !failed && items.length === 0 && (
        <p className="text-[11px] text-zinc-600 dark:text-zinc-400">暂无策略键</p>
      )}

      <ul className="flex flex-col gap-1.5">
        {items.map((it) => {
          const v = verifyByKey.get(it.strategy_key);
          const w = it.window;
          const winRate = typeof w?.win_rate === "number" ? `${(w.win_rate * 100).toFixed(0)}%` : "—";
          return (
            <li
              key={it.strategy_key}
              className="rounded-lg border border-zinc-100 px-2 py-1.5 dark:border-zinc-800"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="text-[11px] text-zinc-800 dark:text-zinc-200">
                  {it.name || it.strategy_key}
                  <span className="ml-1 font-mono text-[10px] text-zinc-500">{it.strategy_key}</span>
                </span>
                <StatusBadge status={it.status} />
              </div>

              <div className="mt-0.5 flex flex-wrap gap-x-2 text-[10px] text-zinc-600 dark:text-zinc-400">
                {it.lifecycle && <span>生命周期：{LIFECYCLE_LABEL[it.lifecycle] ?? it.lifecycle}</span>}
                {it.basis && (
                  <span title="market_neutral 测 alpha 衰减；absolute 测策略自身变差，两者不可互相解释">
                    口径：<span className="font-mono">{it.basis}</span>
                  </span>
                )}
                {w && (
                  <span>
                    窗口 {w.groups ?? "—"} 组合日 · {w.total_picks ?? "—"} 笔 · 胜率 {winRate}
                  </span>
                )}
              </div>

              {it.note && (
                <p className="mt-0.5 text-[10px] leading-relaxed text-zinc-600 dark:text-zinc-400">
                  {it.note}
                </p>
              )}

              <p className="mt-0.5 text-[10px] text-zinc-600 dark:text-zinc-400">
                {v?.available ? (
                  <>
                    核验：<span className="font-mono">{v.verdict ?? "—"}</span>
                    {v.stale && <span className="ml-1 text-amber-600 dark:text-amber-400">（产物超期）</span>}
                    {v.headline && <span className="ml-1">— {v.headline}</span>}
                  </>
                ) : (
                  <>核验：尚无产物（{v?.reason ?? "未跑过核验"}）</>
                )}
              </p>
            </li>
          );
        })}
      </ul>

      <p className="mt-1 text-[10px] text-zinc-500">
        状态为「判不出」时既不代表健康也不代表失效；核验结论来自离线重算（duckdb 全历史），
        随重跑更新。
      </p>
    </div>
  );
}
