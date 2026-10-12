"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { getRealPositions, type RealPositionsPayload } from "@/lib/api";
import { usePollingFetch } from "@/hooks/use-polling-fetch";

/**
 * 真实持仓聚合数据的共用 hook（评审 M3，2026-09-01）。
 *
 * 每个启用实例持有一份轮询。工作台把自身结果与 reload 传给详情，
 * 详情关闭自己的读取；独立详情仍自行读取。
 * 写操作（记一笔/修正/删除）后 reload 即时刷新实际读取所有者。
 */
export interface RealPositionsResource {
  data: RealPositionsPayload | null;
  error: string | null;
  reload: () => Promise<void>;
}

export function useRealPositions(pollMs = 15_000, options?: { enabled?: boolean }): RealPositionsResource {
  const [data, setData] = useState<RealPositionsPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const enabled = options?.enabled !== false;
  const scopeRef = useRef<{ alive: boolean } | null>(null);
  const requestRef = useRef(0);
  useEffect(() => {
    if (!enabled) return;
    const scope = { alive: true };
    scopeRef.current = scope;
    return () => { scope.alive = false; };
  }, [enabled]);

  const load = useCallback(async () => {
    const scope = scopeRef.current;
    if (!scope?.alive) return;
    const request = ++requestRef.current;
    try {
      const next = await getRealPositions();
      if (!scope.alive || scope !== scopeRef.current || request !== requestRef.current) return;
      setData(next);
      setError(null);
    } catch (e) {
      if (!scope.alive || scope !== scopeRef.current || request !== requestRef.current) return;
      setError((e as Error).message);
    }
  }, []);

  usePollingFetch(load, pollMs, undefined, options);

  return { data, error, reload: load };
}
