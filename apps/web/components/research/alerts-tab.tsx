"use client";

import { useCallback, useMemo, useRef, useState } from "react";
import { Panel } from "@/components/panel";
import { FilterMenu } from "@/components/ui/filter-menu";
import { HugeiconsIcon } from "@hugeicons/react";
import Tick02Icon from "@hugeicons/core-free-icons/Tick02Icon";
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
  const [loading, setLoading] = useState(true);
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
  const [submitting, setSubmitting] = useState(false);
  const [busyRule, setBusyRule] = useState<number | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<AlertRule | null>(null);
  const [mutationError, setMutationError] = useState<string | null>(null);
  const [feedback, setFeedback] = useState("");

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (submitting) return;
    setSubmitting(true);
    setMutationError(null);
    setFeedback("");
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
      setFeedback("规则已创建。实际触发与送达以后台记录为准。");
      await load();
    } catch (err) {
      setMutationError(err instanceof Error ? err.message : "创建失败，请重试");
    } finally {
      setSubmitting(false);
    }
  }

  async function toggleEnabled(rule: AlertRule) {
    if (busyRule !== null) return;
    setBusyRule(rule.id);
    setMutationError(null);
    setFeedback("");
    try {
      await updateAlertRule(rule.id, { enabled: !rule.enabled });
      setFeedback(`规则「${rule.name}」已${rule.enabled ? "停用" : "启用"}。`);
      await load();
    } catch (err) {
      setMutationError(err instanceof Error ? err.message : "更新失败，请重试");
    } finally {
      setBusyRule(null);
    }
  }

  async function remove() {
    if (!deleteTarget || busyRule !== null) return;
    const target = deleteTarget;
    setBusyRule(target.id);
    setMutationError(null);
    setFeedback("");
    try {
      await deleteAlertRule(target.id);
      setDeleteTarget(null);
      setFeedback(`已删除规则「${target.name}」。已有触发记录继续保留。`);
      await load();
    } catch (err) {
      setMutationError(err instanceof Error ? err.message : "删除失败，请重试或取消");
    } finally {
      setBusyRule(null);
    }
  }

  return (
    <div className="alerts-management flex h-full min-h-0 min-w-0 flex-col gap-3">
      <div className="flex shrink-0 items-center justify-between">
        <h2 className="text-base font-medium">提醒规则与记录</h2>
        <span className="text-xs text-zinc-600 dark:text-zinc-400">每 10 秒刷新</span>
      </div>

      {error && <div role="alert" className="flex shrink-0 flex-wrap items-center gap-2 rounded-lg bg-[var(--control-surface)] px-3 py-2 text-xs text-[var(--ui-ink)]">读取失败，保留结果仅作上次记录：{error}<button className="quiet-action" onClick={() => void load()}>重试读取</button></div>}
      {mutationError && <p role="alert" className="shrink-0 rounded-lg bg-[var(--control-surface)] px-3 py-2 text-xs text-[var(--ui-ink)]">操作未完成：{mutationError}</p>}
      {feedback && <p role="status" className="shrink-0 text-xs text-[var(--ui-muted)]">{feedback}</p>}

      <div className="grid min-h-0 min-w-0 flex-1 grid-cols-1 grid-rows-[minmax(0,0.9fr),minmax(0,1.1fr)] gap-3 lg:grid-cols-[300px,minmax(0,1fr)] lg:grid-rows-[minmax(0,1fr)]">
        <Panel title="新建规则" className="min-h-0 h-full" bodyClassName="overflow-auto p-3">
          <form onSubmit={submit} className="flex min-w-0 flex-col gap-3 text-sm">
            <fieldset disabled={loading || submitting} className="flex min-w-0 flex-col gap-3 [&_.filter-trigger]:h-9 [&_.filter-trigger]:min-h-9 [&_.filter-trigger]:w-full">
            <div>
              <label htmlFor="alert-rule-name" className="mb-1 block text-xs text-zinc-600 dark:text-zinc-400">名称</label>
              <input id="alert-rule-name"
                value={form.name}
                onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                required
                className="h-9 w-full rounded-md border border-zinc-200 bg-transparent px-3 outline-none focus:border-up/60 dark:border-zinc-700"
                placeholder="如：茅台突破 1300"
              />
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div className="flex flex-col justify-end">
                <FilterMenu<AlertConditionType> label="条件" value={form.condition_type} options={Object.entries(CONDITION_LABEL).map(([key, label]) => ({key: key as AlertConditionType, label}))} onChange={condition => setForm(current => ({...current, condition_type: condition}))} />
              </div>
              <div>
                <label htmlFor="alert-threshold" className="mb-1 block text-xs text-zinc-600 dark:text-zinc-400">阈值</label>
                <input id="alert-threshold"
                  type="number"
                  step="any"
                  value={form.threshold}
                  onChange={(e) => setForm((f) => ({ ...f, threshold: parseFloat(e.target.value) || 0 }))}
                  required
                  className="h-9 w-full rounded-md border border-zinc-200 bg-transparent px-3 outline-none focus:border-up/60 dark:border-zinc-700"
                />
              </div>
            </div>
            <FilterMenu<AlertScope> label="范围" value={form.scope ?? "watchlist"} options={[
              {key: "watchlist", label: "全部自选"}, {key: "symbols", label: "指定标的"}, {key: "all", label: "全市场", title: "仅检查已有行情，覆盖受数据源范围限制"},
            ]} onChange={scope => setForm(current => ({...current, scope}))} />
            {form.scope === "symbols" && (
              <div>
                <label htmlFor="alert-symbols" className="mb-1 block text-xs text-zinc-600 dark:text-zinc-400">标的（逗号分隔）</label>
                <input id="alert-symbols"
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
              <label htmlFor="alert-cooldown" className="mb-1 block text-xs text-zinc-600 dark:text-zinc-400">冷却（秒）</label>
              <input id="alert-cooldown"
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
                  <label key={ch} className="channel-option flex items-center gap-1 text-xs">
                    <input
                      type="checkbox"
                      checked={form.channels?.includes(ch)}
                      onChange={(e) => {
                        const set = new Set(form.channels || []);
                        if (e.target.checked) set.add(ch);
                        else set.delete(ch);
                        setForm((f) => ({ ...f, channels: Array.from(set) }));
                      }}
                      className="sr-only"
                    />
                    <span className="channel-check" aria-hidden="true"><HugeiconsIcon icon={Tick02Icon} size={12} strokeWidth={2} /></span>
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
            <button
              type="submit"
              disabled={loading || submitting}
              className="quiet-action mt-1 self-start bg-[var(--key-face)] px-3 text-xs font-medium text-[var(--key-ink)] disabled:opacity-50"
            >
              {submitting ? "创建中…" : "创建规则"}
            </button>
            </fieldset>
          </form>
        </Panel>

        <div className="flex min-h-0 min-w-0 flex-col gap-3">
          <Panel title={`规则列表 (${rules.length})`} className="min-h-0 flex-1" bodyClassName="overflow-auto">
            {deleteTarget && <form onSubmit={event => {event.preventDefault(); void remove();}} className="flex flex-wrap items-center gap-2 border-b border-[var(--ui-line)] bg-[var(--control-surface)] px-3 py-3 text-xs">
              <p className="min-w-0 flex-1 basis-full leading-relaxed">删除规则「{deleteTarget.name}」？已有触发记录会继续保留。</p>
              <button type="submit" disabled={busyRule !== null} className="quiet-action text-[var(--tick-up-from)]">{busyRule === deleteTarget.id ? "删除中…" : "确认删除"}</button>
              <button type="button" disabled={busyRule !== null} onClick={() => {setDeleteTarget(null); setMutationError(null);}} className="quiet-action">取消删除</button>
            </form>}
            {loading ? <p role="status" className="px-4 py-6 text-xs text-[var(--ui-muted)]">正在读取规则…</p> : rules.length === 0 ? (
              <p className="px-4 py-6 text-center text-sm text-zinc-600 dark:text-zinc-400">{error ? "规则读取失败，当前是否为空尚未确认。请重试读取。" : "暂无规则。"}</p>
            ) : (
              <table className="w-full min-w-[680px] text-xs [&_td]:align-top [&_th]:whitespace-nowrap">
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
                          disabled={busyRule !== null}
                          aria-pressed={r.enabled}
                          aria-label={`${r.enabled ? "停用" : "启用"}规则 ${r.name}`}
                          className={`quiet-action rounded-md px-2 py-0.5 ${r.enabled ? "bg-up/10 text-up-ink dark:text-up" : "bg-zinc-100 text-zinc-600 dark:text-zinc-400 dark:bg-zinc-800"}`}
                        >
                          {busyRule === r.id && deleteTarget?.id !== r.id ? "更新中…" : r.enabled ? "启用" : "停用"}
                        </button>
                      </td>
                      <td className="px-3 py-2">
                        <button disabled={busyRule !== null} onClick={() => {setDeleteTarget(r); setMutationError(null); setFeedback("");}} className="quiet-action text-zinc-600 dark:text-zinc-400 hover:text-red-400" aria-label={`删除规则 ${r.name}`}>
                          删除
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Panel>

          <Panel title={`最近触发记录 (${events.length})`} className="min-h-0 flex-1" bodyClassName="overflow-auto">
            {loading ? <p role="status" className="px-4 py-6 text-xs text-[var(--ui-muted)]">正在读取触发记录…</p> : events.length === 0 ? (
              <p className="px-4 py-6 text-center text-sm text-zinc-600 dark:text-zinc-400">{error ? "触发记录读取失败，不能判断当前没有触发。" : "暂无触发。"}</p>
            ) : (
              <table className="w-full min-w-[680px] text-xs [&_td]:align-top [&_th]:whitespace-nowrap">
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
                        {e.symbol === "000000" ? <span>全局事件</span> : <StockLink symbol={e.symbol} title="查看行情详情">
                          {e.symbol}
                        </StockLink>}
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
                        <span className="text-zinc-600 dark:text-zinc-400">{e.triage?.model === "llm_fallback" ? "规则提醒 · 本条未经过AI判读" : e.triage ? "已判读" : "规则触发 · 未取得AI判读"}</span>
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
                  本次读取中已挡事件（{blocked.length}）· 展开核对降噪依据
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
