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
import { useResource } from "@/hooks/use-resource";
import {ConditionList, ExplanationMap, NarrativeText} from "@/components/ui/explanation-map";

import {
  getStrategyHealth,
  getStrategyRegistry,
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
  // 健康窗口与离线核验产物按 strategy_key 关联，没有共同快照版本。
  const health = useResource(getStrategyHealth, { marketHours: false });
  const registryResource = useResource(getStrategyRegistry, { marketHours: false });
  const items = health.data?.strategies ?? [];
  const registry = registryResource.data ?? [];
  const counts = health.data?.counts;
  const caveat = health.data?.caveat ?? "";
  const loaded = health.data !== undefined;
  function refresh() {
    health.refresh();
    registryResource.refresh();
  }

  // 登记册按 key 索引，取核验结论（S2-11：状态背后的可回查证据）
  const verifyByKey = new Map(registry.map((r) => [r.key, r.verification]));

  return (
    <div className="flex h-full min-h-0 flex-col gap-2 overflow-y-auto">
      <div className="flex flex-wrap items-center gap-2 text-[11px] text-zinc-600 dark:text-zinc-400">
        <span>
          共 {loaded ? counts?.total ?? items.length : "—"} 个策略键 · 可评估 {counts?.evaluable ?? "—"} ·
          需关注 {counts?.attention ?? "—"}
        </span>
        <button data-action="secondary"
          type="button"
          onClick={refresh}
        >
          刷新
        </button>
      </div>

      {caveat && (
        <ConditionList label="健康判读边界" items={[caveat]} tone="warning"/>
      )}

      {[{ label: "策略健康", resource: health }, { label: "核验登记册", resource: registryResource }].map(({ label, resource }) => resource.error != null &&
        <p key={label} role="alert" className="rounded border border-rose-200 bg-rose-50 px-2 py-1 text-[11px] text-rose-700 dark:border-rose-900 dark:bg-rose-950/40 dark:text-rose-300">{label}加载失败：{resource.error instanceof Error ? resource.error.message : "接口异常"}。{resource.data !== undefined ? "保留上次读取结果，当前状态未确认。" : "当前状态未确认。"}</p>
      )}

      {loaded && !health.error && items.length === 0 && (
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
                  <span className="ml-1 font-mono text-[10px] text-zinc-600 dark:text-zinc-400">{it.strategy_key}</span>
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
                <ExplanationMap label="健康依据" identity={it.strategy_key} steps={[
                  {id:"note",label:"判读说明",icon:"source",content:<NarrativeText text={it.note} label="原始判读说明"/>},
                  {id:"window",label:"样本与口径",icon:"response",content:<ConditionList label="采样事实" items={[`口径：${it.basis || "未记录"}`,`窗口：${w?.groups ?? "未记录"} 组合日；${w?.total_picks ?? "未记录"} 笔；胜率 ${winRate}。`,"market_neutral 测 alpha 衰减；absolute 测策略自身变差，两者不可互相解释。"]}/>},
                  {id:"limit",label:"核验边界",icon:"risk",tone:"warning",content:<ConditionList label="不能混同的结论" tone="warning" items={["状态为判不出，既不代表健康，也不代表失效。","健康窗口与离线核验按策略键关联，不是共同版本的同一快照。",...(v?.headline ? [v.headline] : [])]}/>},
                ]}/>
              )}

              <p className="mt-0.5 text-[10px] text-zinc-600 dark:text-zinc-400">
                {registryResource.data === undefined ? (
                  <>核验：{registryResource.error ? "登记册读取失败，核验结论未知" : "登记册正在读取，核验结论未知"}</>
                ) : v?.available ? (
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

      <p className="mt-1 text-[10px] text-zinc-600 dark:text-zinc-400">
        状态为「判不出」时既不代表健康也不代表失效；核验结论来自离线重算（duckdb 全历史），
        随重跑更新。
      </p>
    </div>
  );
}
