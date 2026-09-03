"use client";

import { useCallback, useState } from "react";
import { getRealPositions, type RealPositionsPayload } from "@/lib/api";
import { usePollingFetch } from "@/hooks/use-polling-fetch";

/**
 * 真实持仓聚合数据的共用 hook（评审 M3，2026-09-01）。
 *
 * 此前工作台（取持仓标的列表进 WS 订阅）与详情面板 RealPositionPanel
 * （完整持仓表）各自维护一份 15s 轮询——收敛到这里：一份轮询驱动所有消费方。
 * 写操作（记一笔/修正/删除）后调用返回的 reload() 即时刷新本实例；
 * 其他实例（跨组件树）由 15s 轮询兜底——2026-09-01 简化：原 realChanged
 * 全局事件移除，轮询兜底本就是该数据的设计口径。
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

  usePollingFetch(load, pollMs);

  return { data, error, reload: load };
}
