"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Panel } from "@/components/panel";
import { StockLink } from "@/components/stock-link";
import { usePollingFetch } from "@/hooks/use-polling-fetch";
import {
  createAlertRule,
  deleteAlertRule,
  getAlertChannels,
  listAlertEvents,
  listAlertRules,
  updateAlertRule,
  type AlertConditionType,
  type AlertEvent,
  type AlertRule,
  type AlertRuleCreate,
  type AlertScope,
} from "@/lib/api";
import { pctColor } from "@/lib/format";

/** 研究页 · 预警 tab（原 /alerts 页迁移，2026-09-01 系统重构）。 */

const CONDITION_LABEL: Record<AlertConditionType, string> = {
  price_above: "现价 ≥",
  price_below: "现价 ≤",
  change_pct_above: "涨跌幅 ≥",
  change_pct_below: "涨跌幅 ≤",
};

const SCOPE_LABEL: Record<AlertScope, string> = {
  watchlist: "全部自选",
  symbols: "指定标的",
  all: "全市场（有行情即检）",
};

export function AlertsTab() {
  const [rules, setRules] = useState<AlertRule[]>([]);
  const [events, setEvents] = useState<AlertEvent[]>([]);
  const [channels, setChannels] = useState<string[]>([]);
  const [channelConfig, setChannelConfig] = useState<Record<string, boolean>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const didLoadRef = useRef(false);
  const load = useCallback(async () => {
    // 平滑刷新（2026-09-09 用户反馈：轮询时页面闪烁）——仅首次加载走 loading
    // 骨架；轮询时静默拉取，数据变化才 setState（JSON 浅比较跳过相同数据，
    // 消除无谓重渲染与 sticky thead 抖动）。
    const first = !didLoadRef.current;
    didLoadRef.current = true;
    try {
      if (first) setLoading(true);
      const [r, e, ch] = await Promise.all([
        listAlertRules(),
        listAlertEvents(30),
        getAlertChannels(),
      ]);
      setRules((prev) => (JSON.stringify(prev) === JSON.stringify(r) ? prev : r));
      setEvents((prev) => (JSON.stringify(prev) === JSON.stringify(e) ? prev : e));
      setChannels(ch.available);
      setChannelConfig(ch.configured ?? {});
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载失败");
    } finally {
      if (first) setLoading(false);
    }
  }, []);

  // `marketHours: false`（2026-09-12 评审 R-7）：预警规则/事件/通道**不是行情数据**，
  // 走 `useResource` 默认的「盘外 ×5 降频 + 封顶 120s」会让盘后新产生的告警最多晚 2 分钟
  // 才出现——而盘后恰恰是复盘告警的高峰。关掉降频，维持 10s 恒定节奏。
  usePollingFetch(load, 10_000, undefined, { marketHours: false });

  // P1-36：判读层「已挡（ignore）」事件——被挡清单可见，才能判断"是不是挡多了"。
  const blocked = useMemo(
    () => events.filter((e) => e.triage?.verdict === "ignore"),
    [events],
  );

  const [form, setForm] = useState<AlertRuleCreate>({
    name: "",
    condition_type: "price_above",
    threshold: 0,
    symbols: [],
    scope: "watchlist",
    cooldown_seconds: 300,
    channels: ["in_app", "log"],
    enabled: true,
  });
  const [symbolInput, setSymbolInput] = useState("");

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    try {
      await createAlertRule({
        ...form,
        symbols: form.scope === "symbols" ? form.symbols : [],
      });
      setForm({
        name: "",
        condition_type: "price_above",
        threshold: 0,
        symbols: [],
        scope: "watchlist",
        cooldown_seconds: 300,
        channels: ["in_app", "log"],
        enabled: true,
      });
      setSymbolInput("");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "创建失败");
    }
  }

  async function toggleEnabled(rule: AlertRule) {
    try {
      await updateAlertRule(rule.id, { enabled: !rule.enabled });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "更新失败");
    }
  }

  async function remove(id: number) {
    if (!confirm("删除此规则？")) return;
    try {
      await deleteAlertRule(id);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "删除失败");
    }
  }

  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      <div className="flex shrink-0 items-center justify-between">
        <h2 className="text-base font-semibold">预警通知</h2>
        <span className="text-xs text-zinc-600 dark:text-zinc-400">判读即终态 · 自动 10 秒刷新</span>
      </div>

      {error && <div className="shrink-0 rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-2 text-sm text-amber-800 dark:text-amber-600">{error}</div>}

      <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 lg:grid-cols-[360px,minmax(0,1fr)]">
        <Panel title="新建规则" className="flex flex-col gap-3 overflow-auto">
          <form onSubmit={submit} className="flex flex-col gap-3 text-sm">
            <div>
              <label className="mb-1 block text-xs text-zinc-600 dark:text-zinc-400">名称</label>
              <input
                value={form.name}
                onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                required
                className="h-9 w-full rounded-md border border-zinc-200 bg-transparent px-3 outline-none focus:border-up/60 dark:border-zinc-700"
                placeholder="如：茅台突破 1300"
              />
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="mb-1 block text-xs text-zinc-600 dark:text-zinc-400">条件</label>
                <select
                  value={form.condition_type}
                  onChange={(e) => setForm((f) => ({ ...f, condition_type: e.target.value as AlertConditionType }))}
                  className="h-9 w-full rounded-md border border-zinc-200 bg-transparent px-3 outline-none focus:border-up/60 dark:border-zinc-700"
                >
                  <option value="price_above">现价 ≥</option>
                  <option value="price_below">现价 ≤</option>
                  <option value="change_pct_above">涨跌幅 ≥</option>
                  <option value="change_pct_below">涨跌幅 ≤</option>
                </select>
              </div>
              <div>
                <label className="mb-1 block text-xs text-zinc-600 dark:text-zinc-400">阈值</label>
                <input
                  type="number"
                  step="any"
                  value={form.threshold}
                  onChange={(e) => setForm((f) => ({ ...f, threshold: parseFloat(e.target.value) || 0 }))}
                  required
                  className="h-9 w-full rounded-md border border-zinc-200 bg-transparent px-3 outline-none focus:border-up/60 dark:border-zinc-700"
                />
              </div>
            </div>
            <div>
              <label className="mb-1 block text-xs text-zinc-600 dark:text-zinc-400">范围</label>
              <select
                value={form.scope}
                onChange={(e) => setForm((f) => ({ ...f, scope: e.target.value as AlertScope }))}
                className="h-9 w-full rounded-md border border-zinc-200 bg-transparent px-3 outline-none focus:border-up/60 dark:border-zinc-700"
              >
                <option value="watchlist">全部自选</option>
                <option value="symbols">指定标的</option>
                <option value="all">全市场</option>
              </select>
            </div>
            {form.scope === "symbols" && (
              <div>
                <label className="mb-1 block text-xs text-zinc-600 dark:text-zinc-400">标的（逗号分隔）</label>
                <input
                  value={symbolInput}
                  onChange={(e) => {
                    setSymbolInput(e.target.value);
                    setForm((f) => ({
                      ...f,
                      symbols: e.target.value.split(",").map((s) => s.trim()).filter(Boolean),
                    }));
                  }}
                  placeholder="600519,000001"
                  className="h-9 w-full rounded-md border border-zinc-200 bg-transparent px-3 outline-none focus:border-up/60 dark:border-zinc-700"
                />
              </div>
            )}
            <div>
              <label className="mb-1 block text-xs text-zinc-600 dark:text-zinc-400">冷却（秒）</label>
              <input
                type="number"
                min={0}
                value={form.cooldown_seconds}
                onChange={(e) => setForm((f) => ({ ...f, cooldown_seconds: parseInt(e.target.value) || 0 }))}
                className="h-9 w-full rounded-md border border-zinc-200 bg-transparent px-3 outline-none focus:border-up/60 dark:border-zinc-700"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs text-zinc-600 dark:text-zinc-400">通知通道</label>
              <div className="flex flex-wrap gap-2">
                {channels.map((ch) => (
                  <label key={ch} className="flex items-center gap-1 text-xs">
                    <input
                      type="checkbox"
                      checked={form.channels?.includes(ch)}
                      onChange={(e) => {
                        const set = new Set(form.channels || []);
                        if (e.target.checked) set.add(ch);
                        else set.delete(ch);
                        setForm((f) => ({ ...f, channels: Array.from(set) }));
                      }}
                      className="accent-up"
                    />
                    {ch}
                    {channelConfig[ch] === false && (
                      <span
                        title="该通道未完成配置（如飞书 webhook），触发时会跳过并在后端日志告警，不会伪装成功"
                        className="rounded bg-amber-500/15 px-1 text-[10px] text-amber-800 dark:text-amber-400"
                      >
                        未配置
                      </span>
                    )}
                  </label>
                ))}
              </div>
            </div>
            {/* `bg-up-deep/90` 已去掉那 10% 透明度（2026-09-11 P2-24）：90% 叠在浅色卡片上被稀释成
                rgb(228,51,90)，白字对比度从 4.70 掉到 4.28（< AA 4.5）；实心后与其余三处 up-deep
                按钮一致，`hover:bg-up-deep` 也随之冗余（悬停反馈由全局 :active 按压 + transition 承担）。 */}
            <button
              type="submit"
              disabled={loading}
              className="mt-1 rounded-md bg-up-deep px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
            >
              创建规则
            </button>
          </form>
        </Panel>

        <div className="flex min-h-0 flex-col gap-3">
          <Panel title={`规则列表 (${rules.length})`} className="max-h-[45%] shrink-0 overflow-auto">
            {rules.length === 0 ? (
              <p className="px-4 py-6 text-center text-sm text-zinc-600 dark:text-zinc-400">暂无规则。</p>
            ) : (
              <table className="w-full text-xs">
                <thead className="sticky top-0 bg-zinc-50 text-zinc-600 dark:text-zinc-400 dark:bg-zinc-900">
                  <tr>
                    <th className="px-3 py-2 text-left font-medium">名称</th>
                    <th className="px-3 py-2 text-left font-medium">条件</th>
                    <th className="px-3 py-2 text-left font-medium">范围</th>
                    <th className="px-3 py-2 text-left font-medium">冷却</th>
                    <th className="px-3 py-2 text-left font-medium">通道</th>
                    <th className="px-3 py-2 text-left font-medium">状态</th>
                    <th className="px-3 py-2 text-left font-medium">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {rules.map((r) => (
                    <tr key={r.id} className="border-b border-zinc-100 last:border-0 dark:border-zinc-800/60">
                      <td className="px-3 py-2">{r.name}</td>
                      <td className="px-3 py-2">
                        {CONDITION_LABEL[r.condition_type]} {r.threshold}
                      </td>
                      <td className="px-3 py-2">{SCOPE_LABEL[r.scope]}</td>
                      <td className="px-3 py-2">{r.cooldown_seconds}s</td>
                      <td className="px-3 py-2">{r.channels.join(", ")}</td>
                      <td className="px-3 py-2">
                        <button
                          onClick={() => void toggleEnabled(r)}
                          className={`rounded-full px-2 py-0.5 ${r.enabled ? "bg-up/10 text-up-ink dark:text-up" : "bg-zinc-100 text-zinc-600 dark:text-zinc-400 dark:bg-zinc-800"}`}
                        >
                          {r.enabled ? "启用" : "停用"}
                        </button>
                      </td>
                      <td className="px-3 py-2">
                        <button onClick={() => void remove(r.id)} className="text-zinc-600 dark:text-zinc-400 hover:text-red-400">
                          删除
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Panel>

          <Panel title={`触发记录 (${events.length})`} className="min-h-0 flex-1 overflow-auto">
            {events.length === 0 ? (
              <p className="px-4 py-6 text-center text-sm text-zinc-600 dark:text-zinc-400">暂无触发。</p>
            ) : (
              <table className="w-full text-xs">
                <thead className="sticky top-0 bg-zinc-50 text-zinc-600 dark:text-zinc-400 dark:bg-zinc-900">
                  <tr>
                    <th className="px-3 py-2 text-left font-medium">时间</th>
                    <th className="px-3 py-2 text-left font-medium">标的</th>
                    <th className="px-3 py-2 text-left font-medium">规则</th>
                    <th className="px-3 py-2 text-left font-medium">触发值</th>
                    <th className="px-3 py-2 text-left font-medium">阈值</th>
                    <th className="px-3 py-2 text-left font-medium">通道</th>
                    <th className="px-3 py-2 text-left font-medium">状态</th>
                  </tr>
                </thead>
                <tbody>
                  {events.map((e) => (
                    <tr key={e.id} className="border-b border-zinc-100 last:border-0 dark:border-zinc-800/60">
                      <td className="px-3 py-2 font-mono text-zinc-600 dark:text-zinc-400">
                        {new Date(e.triggered_at).toLocaleTimeString("zh-CN")}
                      </td>
                      <td className="px-3 py-2 font-mono">
                        <StockLink symbol={e.symbol} title="查看行情详情">
                          {e.symbol}
                        </StockLink>
                      </td>
                      <td className="px-3 py-2">{rules.find((r) => r.id === e.rule_id)?.name ?? e.rule_id}</td>
                      <td className={`px-3 py-2 font-mono tabular-nums ${e.snapshot ? pctColor(e.snapshot.change_pct) : ""}`}>
                        {e.trigger_value.toFixed(2)}
                      </td>
                      <td className="px-3 py-2 font-mono tabular-nums text-zinc-600 dark:text-zinc-400">{e.threshold}</td>
                      <td className="px-3 py-2 text-zinc-600 dark:text-zinc-400">{e.delivered_channels.join(", ")}</td>
                      <td className="px-3 py-2">
                        {/* 2026-09-08 用户指令：触发记录状态不再需要确认——判读完成即自动置
                            acknowledged，此处只读展示终态，移除人工「确认」按钮 */}
                        <span className="text-zinc-600 dark:text-zinc-400">已判读</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}

            {/* P1-36：判读挡了什么必须可见——否则无法判断"是不是挡多了"。
                只列出 verdict=ignore 的（notify 已在悬浮球、escalate 另见任务中心）；
                `llm_fallback` 单独标出，不伪装成 AI 判断。 */}
            {blocked.length > 0 && (
              <details className="border-t border-zinc-100 dark:border-zinc-800/60">
                <summary className="cursor-pointer select-none px-3 py-2 text-xs text-zinc-600 dark:text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-300">
                  今日已挡事件（{blocked.length}）· 展开查看已降噪告警
                </summary>
                <ul className="space-y-1 px-3 pb-3 text-[11px]">
                  {blocked.map((e) => (
                    <li
                      key={e.id}
                      className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 rounded bg-zinc-50 px-2 py-1 dark:bg-zinc-900/60"
                    >
                      <span className="font-mono text-zinc-600 dark:text-zinc-400">
                        {new Date(e.triggered_at).toLocaleTimeString("zh-CN")}
                      </span>
                      <span className="font-mono">{e.symbol}</span>
                      <span className="text-zinc-600 dark:text-zinc-400">
                        {rules.find((r) => r.id === e.rule_id)?.name ?? e.rule_id}
                      </span>
                      <span className="text-zinc-600 dark:text-zinc-300">{e.triage?.reason || "（无理由记录）"}</span>
                      {e.triage?.model === "llm_fallback" && (
                        <span className="rounded bg-amber-500/15 px-1 text-[10px] text-amber-800 dark:text-amber-400">
                          判读降级
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </Panel>
        </div>
      </div>
    </div>
  );
}
