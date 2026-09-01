"use client";

import { useCallback, useEffect, useState } from "react";
import { getRealPositions, type RealPositionsPayload } from "@/lib/api";
import { APP_EVENTS, onAppEvent } from "@/lib/events";

/**
 * 真实持仓聚合数据的共用 hook（评审 M3，2026-09-01）。
 *
 * 此前工作台（取持仓标的列表进 WS 订阅）与详情面板 RealPositionPanel
 * （完整持仓表）各自维护一份 15s 轮询 + realChanged 监听——同一端点
 * 双份轮询、双份订阅代码。收敛到这里：一份轮询驱动所有消费方。
 * 记一笔成交后 emitAppEvent(realChanged) 即可让所有挂载方即时刷新。
 */
export function useRealPositions(pollMs = 15_000) {
  const [data, setData] = useState<RealPositionsPayload | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await getRealPositions());
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    void load();
    const t = setInterval(() => void load(), pollMs);
    const off = onAppEvent(APP_EVENTS.realChanged, () => void load());
    return () => {
      clearInterval(t);
      off();
    };
  }, [load, pollMs]);

  return { data, error, reload: load };
}
