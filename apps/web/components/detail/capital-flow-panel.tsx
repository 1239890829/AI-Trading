"use client";

/**
 * 资金图自治面板（2026-09-09 需求 2）
 *
 * 工作台个股页原先在 stock-detail 壳里加载 `capital-flow` 再喂给 FlowChart（纯展示），
 * 猎场卡片想复用就得整壳复制。这里把「取数 + 三态 + 展示」封成一个组件：
 * 传入 symbol 即可，弹窗/页面都能直接挂。
 *
 * 三态纪律：undefined=尚未拉到（骨架）；null=拉过确认无（空态文案）；有值=渲染。
 * 与 stock-detail 的三态口径一致，不再把"加载中"渲染成"暂无资金数据"。
 */
import { useEffect, useState } from "react";

import { getCapitalFlow } from "@/lib/api";
import { FlowChart, type CapitalFlow } from "@/components/detail/flow-chart";

export function CapitalFlowPanel({ symbol, days = 30 }: { symbol: string; days?: number }) {
  const [flow, setFlow] = useState<CapitalFlow | null | undefined>(undefined);

  // 切股/切窗口 → 当帧回到「加载中」（渲染期 adjust-state；原为 effect 内同步
  // setState，会多一帧旧数据残留并触发 react-hooks/set-state-in-effect，P1-27）
  const flowKey = `${symbol}:${days}`;
  const [prevFlowKey, setPrevFlowKey] = useState(flowKey);
  if (flowKey !== prevFlowKey) {
    setPrevFlowKey(flowKey);
    setFlow(undefined);
  }

  useEffect(() => {
    let alive = true;
    getCapitalFlow<CapitalFlow>(symbol, days)
      .then((cf) => {
        if (!alive) return;
        setFlow(cf ?? null);
      })
      .catch(() => alive && setFlow(null));
    return () => {
      alive = false;
    };
  }, [symbol, days]);

  if (flow === undefined) {
    return <p className="py-6 text-center text-xs text-zinc-600 dark:text-zinc-400">资金数据加载中…</p>;
  }
  if (flow === null) {
    return (
      <p className="py-6 text-center text-xs text-zinc-600 dark:text-zinc-400">
        暂无资金流数据（数据源不可用或非交易时段）
      </p>
    );
  }
  return <FlowChart flow={flow} />;
}
