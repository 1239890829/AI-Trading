"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  createChart,
  IChartApi,
  LineData,
  Time,
} from "lightweight-charts";
import { Panel } from "@/components/panel";
import {
  getBacktestStrategies,
  runBacktest,
  type BacktestPayload,
  type StrategyInfo,
} from "@/lib/api";

/** 日线策略回测页（Phase 6 后半）：TDX QFQ 日K + 代码级防泄露引擎。
 * 撮合口径与全部禁令见 docs/backtest-rules.md；结果为统计事实，不构成买卖建议。 */

function pct(v: number): string {
  return `${v > 0 ? "+" : ""}${(v * 100).toFixed(1)}%`;
}

function pctCls(v: number): string {
  if (v > 0) return "text-red-600 dark:text-red-400";
  if (v < 0) return "text-emerald-600 dark:text-emerald-400";
  return "text-zinc-400";
}

export default function BacktestPage() {
  const [strategies, setStrategies] = useState<StrategyInfo[]>([]);
  const [symbol, setSymbol] = useState("600519");
  const [strategyId, setStrategyId] = useState("ma_cross");
  const [paramsText, setParamsText] = useState('{"fast": 5, "slow": 20}');
  const [bars, setBars] = useState("500");
  const [data, setData] = useState<BacktestPayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<{ eq: ReturnType<IChartApi["addLineSeries"]>; bench: ReturnType<IChartApi["addLineSeries"]> } | null>(null);

  useEffect(() => {
    getBacktestStrategies().then(setStrategies).catch(() => {});
  }, []);

  // 净值曲线（data 到位后容器 div 才存在——依赖 [hasData] 保证 chart 在 div 渲染后创建）
  const hasData = !!data;
  useEffect(() => {
    if (!hasData || !containerRef.current) return;
    const chart = createChart(containerRef.current, {
      autoSize: true,
      layout: { background: { color: "transparent" }, textColor: "#a1a1aa" },
      grid: {
        vertLines: { color: "rgba(120,120,130,0.12)" },
        horzLines: { color: "rgba(120,120,130,0.12)" },
      },
      timeScale: { timeVisible: false, borderVisible: false },
    });
    chartRef.current = chart;
    const eq = chart.addLineSeries({ color: "#3b82f6", lineWidth: 2, title: "策略" });
    const bench = chart.addLineSeries({ color: "#71717a", lineWidth: 1, title: "买入持有" });
    seriesRef.current = { eq, bench };
    return () => {
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, [hasData]);

  useEffect(() => {
    if (!data || !seriesRef.current) return;
    const eqData: LineData[] = data.equity.map((p) => ({
      time: p.ts.slice(0, 10) as Time,
      value: p.value,
    }));
    const benchData: LineData[] = data.equity.map((p) => ({
      time: p.ts.slice(0, 10) as Time,
      value: p.benchmark,
    }));
    seriesRef.current.eq.setData(eqData);
    seriesRef.current.bench.setData(benchData);
    chartRef.current?.timeScale().fitContent();
  }, [data]);

  const run = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      let params: Record<string, number> = {};
      try {
        params = paramsText.trim() ? JSON.parse(paramsText) : {};
      } catch {
        throw new Error("参数 JSON 格式错误");
      }
      setData(await runBacktest({ symbol, strategy_id: strategyId, params, bars: Number(bars) || 500 }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [symbol, strategyId, paramsText, bars]);

  const m = data?.metrics;

  return (
    <div className="flex h-full min-h-0 flex-col gap-2">
      {error && (
        <div className="shrink-0 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-1.5 text-xs text-amber-600 dark:text-amber-300">
          {error}
        </div>
      )}

      <Panel
        title="日线策略回测"
        className="min-h-0 flex-1"
        bodyClassName="overflow-hidden flex flex-col"
        extra={<span className="text-[10px] text-zinc-400">统计事实 · 不构成买卖建议</span>}
      >
        {/* 工具条 */}
        <div className="flex shrink-0 flex-wrap items-center gap-x-3 gap-y-1.5 border-b border-zinc-200 px-3 py-2 text-xs dark:border-zinc-800">
          <label className="flex items-center gap-1">
            代码
            <input
              value={symbol}
              onChange={(e) => setSymbol(e.target.value)}
              className="w-20 rounded border border-zinc-300 bg-transparent px-1.5 py-0.5 tabular-nums dark:border-zinc-700"
            />
          </label>
          <label className="flex items-center gap-1">
            策略
            <select
              value={strategyId}
              onChange={(e) => {
                setStrategyId(e.target.value);
                const s = strategies.find((x) => x.id === e.target.value);
                if (s) setParamsText(JSON.stringify(s.params));
              }}
              className="rounded border border-zinc-300 bg-transparent px-1.5 py-0.5 dark:border-zinc-700"
            >
              {strategies.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </label>
          <label className="flex items-center gap-1">
            参数
            <input
              value={paramsText}
              onChange={(e) => setParamsText(e.target.value)}
              className="w-44 rounded border border-zinc-300 bg-transparent px-1.5 py-0.5 font-mono dark:border-zinc-700"
            />
          </label>
          <label className="flex items-center gap-1">
            根数
            <input
              value={bars}
              onChange={(e) => setBars(e.target.value)}
              className="w-16 rounded border border-zinc-300 bg-transparent px-1.5 py-0.5 tabular-nums dark:border-zinc-700"
            />
          </label>
          <button
            onClick={() => void run()}
            disabled={loading}
            className="rounded bg-zinc-900 px-3 py-1 font-medium text-white disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900"
          >
            {loading ? "回测中…" : "运行回测"}
          </button>
          {data && (
            <span className="ml-auto tabular-nums text-zinc-400">
              {data.symbol} · {data.bars_count} 根 · {data.trades.filter((t) => t.ok).length} 笔成交
            </span>
          )}
        </div>

        {/* 指标卡 */}
        {m && (
          <div className="grid shrink-0 grid-cols-4 gap-px border-b border-zinc-200 bg-zinc-200 text-center text-xs dark:border-zinc-800 dark:bg-zinc-800 md:grid-cols-8">
            {[
              { label: "总收益", value: pct(m.total_return), cls: pctCls(m.total_return) },
              { label: "基准", value: pct(m.benchmark_return), cls: pctCls(m.benchmark_return) },
              { label: "超额", value: pct(m.excess_return), cls: pctCls(m.excess_return) },
              { label: "年化", value: pct(m.annual_return), cls: pctCls(m.annual_return) },
              { label: "最大回撤", value: pct(-m.max_drawdown), cls: "text-zinc-500" },
              { label: "Sharpe", value: String(m.sharpe), cls: "text-zinc-500" },
              { label: "胜率", value: `${(m.win_rate * 100).toFixed(0)}%`, cls: "text-zinc-500" },
              { label: "盈亏比", value: String(m.profit_loss_ratio), cls: "text-zinc-500" },
            ].map((c) => (
              <div key={c.label} className="bg-white px-1 py-2 dark:bg-zinc-900">
                <div className="text-[10px] text-zinc-400">{c.label}</div>
                <div className={`mt-0.5 font-semibold tabular-nums ${c.cls}`}>{c.value}</div>
              </div>
            ))}
          </div>
        )}

        {/* 曲线 */}
        <div className="min-h-0 flex-1">
          {data ? (
            <div ref={containerRef} className="h-full w-full" />
          ) : (
            <p className="px-4 py-10 text-center text-sm text-zinc-400">
              输入条件后点击「运行回测」（{loading ? "计算中…" : "500 根日K秒级完成"}）
            </p>
          )}
        </div>

        {/* 交易记录 */}
        {data && data.trades.length > 0 && (
          <div className="max-h-[28%] shrink-0 overflow-auto border-t border-zinc-200 dark:border-zinc-800">
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-zinc-50 text-left text-zinc-400 dark:bg-zinc-900">
                <tr>
                  <th className="px-2 py-1 font-medium">成交日</th>
                  <th className="px-2 py-1 font-medium">方向</th>
                  <th className="px-2 py-1 text-right font-medium">价格</th>
                  <th className="px-2 py-1 text-right font-medium">数量</th>
                  <th className="px-2 py-1 text-right font-medium">费用</th>
                  <th className="px-2 py-1 font-medium">状态</th>
                </tr>
              </thead>
              <tbody>
                {[...data.trades].reverse().map((t, i) => (
                  <tr key={`${t.fill_ts}-${t.side}-${i}`} className="border-t border-zinc-100 dark:border-zinc-800/60">
                    <td className="px-2 py-1 tabular-nums">{t.fill_ts.slice(0, 10)}</td>
                    <td className={`px-2 py-1 ${t.side === "buy" ? "text-red-600 dark:text-red-400" : "text-emerald-600 dark:text-emerald-400"}`}>
                      {t.side === "buy" ? "买入" : "卖出"}
                    </td>
                    <td className="px-2 py-1 text-right tabular-nums">{t.price.toFixed(2)}</td>
                    <td className="px-2 py-1 text-right tabular-nums">{t.qty}</td>
                    <td className="px-2 py-1 text-right tabular-nums text-zinc-500">{t.fee.toFixed(2)}</td>
                    <td className="px-2 py-1 text-zinc-500">
                      {t.ok ? "成交" : <span className="text-amber-500">拒（{t.reason}）</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {data && (
          <div className="shrink-0 border-t border-zinc-200 px-3 py-1.5 text-[10px] text-zinc-400 dark:border-zinc-800">
            {data.notes.join("；")}
          </div>
        )}
      </Panel>
    </div>
  );
}
