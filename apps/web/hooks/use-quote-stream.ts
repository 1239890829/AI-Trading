"use client";

import { useEffect, useRef, useState } from "react";
import { getQuotes, wsBase } from "@/lib/api";
import type { Quote } from "@/types/market";

export type StreamStatus = "connecting" | "live" | "polling" | "closed" | "stale" | "error";

/**
 * 行情流：优先 WebSocket（/ws/quotes），断线自动重连；
 * 连续失败 3 次后降级为 REST 轮询（5s），并在恢复时切回 WS。
 *
 * 订阅更新（2026-09-01 架构方案 P1，修复实锤断点 P2）：后端支持
 * {"action":"subscribe"} 动态切换订阅集——自选集合变化时发送 subscribe 消息
 * 而非整条重连。此前 symbols 每次变化 → effect 重建 → WS 重连，产生行情空窗，
 * 重连失败 3 次还会误入降级轮询。
 */
export function useQuoteStream(symbols: string[]) {
  const [quotes, setQuotes] = useState<Record<string, Quote>>({});
  const [status, setStatus] = useState<StreamStatus>("connecting");

  const key = [...symbols].sort().join(",");
  const hasSymbols = key.length > 0;

  // symbols 的实时值供重连/订阅使用（effect 闭包不可靠，渲染期写 ref 在并发渲染下同样不可靠）
  const symbolsRef = useRef<string[]>(symbols);
  useEffect(() => {
    symbolsRef.current = symbols;
  }, [symbols]);

  const wsRef = useRef<WebSocket | null>(null);
  const connectedRef = useRef(false);

  useEffect(() => {
    if (!hasSymbols || connectedRef.current) return;
    connectedRef.current = true;

    let closed = false;
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
      // 重连时保持当前状态显示（不闪回 connecting 误导用户）
      setStatus((prev) => (retry === 0 ? "connecting" : prev));
      try {
        wsRef.current = new WebSocket(`${wsBase()}/ws/quotes?symbols=${symbolsRef.current.join(",")}`);
      } catch {
        startPolling();
        return;
      }
      const ws = wsRef.current;
      // 半死连接防御（2026-09-01 实测：后端 pong 与推送并发写曾致 writer 静默死亡，
      // 连接 open、ping 有应答、但推送为零——列表冻结在初始值。后端已改为单点发送；
      // 这里再加客户端自愈：32s（2 个心跳周期）内没收到任何消息就主动断开重连，
      // 重连失败 3 次自然落入 REST 轮询兜底）
      let lastMsgAt = Date.now();
      ws.onopen = () => {
        retry = 0;
        stopPolling();
        lastMsgAt = Date.now();
        lastSentKey.current = ""; // 新 socket 允许下一次 key 变化时重发订阅
        setStatus("live");
        pingTimer = setInterval(() => {
          if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
            if (Date.now() - lastMsgAt > 32_000) {
              wsRef.current.close(); // 触发 onclose 重连
              return;
            }
            wsRef.current.send(JSON.stringify({ action: "ping" }));
          }
        }, 15000);
      };
      ws.onmessage = (ev) => {
        lastMsgAt = Date.now();
        try {
          const msg = JSON.parse(ev.data as string) as { type: string; data?: Quote[] };
          // stale：后端推送 quality=stale 的缓存数据（休市 market_closed / 刷新失败），
          // 必须覆盖渲染以显示过期标识。注意这与"WS 断线"无关——消息本身说明连接是通的：
          // 休市显示"休市"，刷新失败显示"数据过期"；恢复 quotes 推送时切回 live
          // （2026-09-01 修复：原实现把 stale 误标成 polling，且开盘后永不恢复 live）。
          if ((msg.type === "snapshot" || msg.type === "quotes" || msg.type === "stale") && msg.data) {
            apply(msg.data);
            const closedData = msg.data.some((q) => q.quality_reasons?.includes("market_closed"));
            if (closedData) {
              // 休市数据可能以 quotes 类型到达（REST 兜底/快照路径），一律置休市态
              setStatus("closed");
            } else if (msg.type === "stale") {
              setStatus("stale");
            } else if (msg.type === "quotes") {
              setStatus("live");
            }
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
        wsRef.current?.close();
      };
    };

    connect();

    return () => {
      closed = true;
      connectedRef.current = false;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (pingTimer) clearInterval(pingTimer);
      stopPolling();
      wsRef.current?.close();
      wsRef.current = null;
    };
  }, [hasSymbols]);

  // 订阅更新：连接存活时发 subscribe 消息切换订阅集（不重连）。
  // ⚠️ 依赖只允许 key（集合的稳定字符串）：symbols 数组每次渲染都是新引用，
  // 放进依赖数组 → 每次渲染重发 subscribe → 后端回快照 → setQuotes →
  // 再渲染 → 死循环（Maximum update depth exceeded，2026-09-01 实测白屏）。
  // 发送内容读 symbolsRef（连接期最新集合），并与 lastSentKey 去重。
  const lastSentKey = useRef<string>("");
  useEffect(() => {
    const ws = wsRef.current;
    if (!connectedRef.current || !ws || ws.readyState !== WebSocket.OPEN) return;
    if (!key || key === lastSentKey.current) return;
    lastSentKey.current = key;
    try {
      ws.send(JSON.stringify({ action: "subscribe", symbols: [...new Set(symbolsRef.current)] }));
    } catch {}
  }, [key]);

  return { quotes, status };
}
