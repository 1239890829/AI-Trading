"use client";

import { useEffect, useRef, useState } from "react";
import { getQuotes, WS_BASE } from "@/lib/api";
import type { Quote } from "@/types/market";

export type StreamStatus = "connecting" | "live" | "polling" | "error";

/**
 * 行情流：优先 WebSocket（/ws/quotes），断线自动重连；
 * 连续失败 3 次后降级为 REST 轮询（5s），并在恢复时切回 WS。
 * mock 数据源的 is_realtime 恒为 false，状态展示由 meta/quality 字段负责。
 */
export function useQuoteStream(symbols: string[]) {
  const [quotes, setQuotes] = useState<Record<string, Quote>>({});
  const [status, setStatus] = useState<StreamStatus>("connecting");
  const key = [...symbols].sort().join(",");
  const symbolsRef = useRef(symbols);
  symbolsRef.current = symbols;

  useEffect(() => {
    if (!key) {
      setQuotes({});
      return;
    }
    let closed = false;
    let ws: WebSocket | null = null;
    let retry = 0;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let pollTimer: ReturnType<typeof setInterval> | null = null;
    let pingTimer: ReturnType<typeof setInterval> | null = null;

    const apply = (list: Quote[]) => {
      setQuotes(Object.fromEntries(list.map((q) => [q.symbol, q])));
    };

    const startPolling = () => {
      if (pollTimer || closed) return;
      setStatus("polling");
      const tick = async () => {
        try {
          apply(await getQuotes(symbolsRef.current));
        } catch {
          setStatus("error");
        }
      };
      void tick();
      pollTimer = setInterval(tick, 5000);
    };

    const stopPolling = () => {
      if (pollTimer) clearInterval(pollTimer);
      pollTimer = null;
    };

    const connect = () => {
      if (closed) return;
      setStatus(retry === 0 ? "connecting" : status);
      try {
        ws = new WebSocket(`${WS_BASE}/ws/quotes?symbols=${key}`);
      } catch {
        startPolling();
        return;
      }
      ws.onopen = () => {
        retry = 0;
        stopPolling();
        setStatus("live");
        pingTimer = setInterval(() => {
          if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ action: "ping" }));
        }, 15000);
      };
      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data as string) as { type: string; data?: Quote[] };
          // stale：数据源故障时后端推送 quality=stale 的缓存数据，必须覆盖渲染以显示过期标识
          if ((msg.type === "snapshot" || msg.type === "quotes" || msg.type === "stale") && msg.data) {
            apply(msg.data);
            if (msg.type === "stale") setStatus("polling");
          }
        } catch {}
      };
      ws.onclose = () => {
        if (pingTimer) clearInterval(pingTimer);
        pingTimer = null;
        if (closed) return;
        retry += 1;
        if (retry >= 3) startPolling();
        reconnectTimer = setTimeout(connect, Math.min(1000 * 2 ** retry, 10000));
      };
      ws.onerror = () => {
        ws?.close();
      };
    };

    connect();

    return () => {
      closed = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (pingTimer) clearInterval(pingTimer);
      stopPolling();
      ws?.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  return { quotes, status };
}
