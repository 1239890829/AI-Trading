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
export function useQuoteStream(symbols: string[], opts?: { throttleMs?: number }) {
  const [quotes, setQuotes] = useState<Record<string, Quote>>({});
  const [status, setStatus] = useState<StreamStatus>("connecting");
  // 已发送的订阅 key（去重用，见下方订阅 effect 注释）
  const [sentKey, setSentKey] = useState("");
  // 展示节流（2026-09-02 用户反馈：自选列表 1Hz 刷新频繁闪烁）：
  // WS 仍按 1Hz 全量接收（内部状态不丢数据），对外 quotes 状态按
  // throttleMs 节流应用（trailing——间隔内的最后一条生效），价格类面板
  // 不必跟着推送节奏逐秒重渲染。首帧（首次应用）不节流，避免白屏等拍。
  // status 不节流：连接态变化必须即时呈现。不传 throttleMs 行为不变。
  const throttleMs = opts?.throttleMs ?? 0;

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
    let openTimer: ReturnType<typeof setTimeout> | null = null;
    let throttleTimer: ReturnType<typeof setTimeout> | null = null;
    let lastAppliedAt = 0;
    let pending: Quote[] | null = null;
    const clearOpenTimer = () => {
      if (openTimer) clearTimeout(openTimer);
      openTimer = null;
    };
    const clearThrottle = () => {
      if (throttleTimer) clearTimeout(throttleTimer);
      throttleTimer = null;
    };
    const applyNow = (list: Quote[]) => {
      lastAppliedAt = Date.now();
      pending = null;
      setQuotes(Object.fromEntries(list.map((q) => [q.symbol, q])));
    };
    const apply = (list: Quote[]) => {
      if (throttleMs <= 0) {
        applyNow(list);
        return;
      }
      pending = list;
      const elapsed = Date.now() - lastAppliedAt;
      if (elapsed >= throttleMs) {
        if (throttleTimer) clearTimeout(throttleTimer);
        throttleTimer = null;
        applyNow(list);
        return;
      }
      if (throttleTimer) return; // 已有待触发的 trailing 应用
      throttleTimer = setTimeout(() => {
        throttleTimer = null;
        if (closed || !pending) return;
        applyNow(pending);
      }, throttleMs - elapsed);
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
      let ws: WebSocket;
      try {
        ws = new WebSocket(`${wsBase()}/ws/quotes?symbols=${symbolsRef.current.join(",")}`);
        wsRef.current = ws;
      } catch {
        startPolling();
        return;
      }
      // 握手超时兜底（2026-09-02 实测 P0）：WebSocket API **没有连接超时**。
      // 当对端接受 TCP 却不完成 upgrade（典型：Next dev 不代理 /backend 的 WS 升级，
      // 请求被静默挂起），onopen/onclose/onerror 三者都不触发 —— 状态永远停在
      // "connecting"，retry 永不递增，REST 轮询降级兜底也永远不会启动。
      // 这里 6s 内未 open 即判定握手失败并主动 close，让既有的重试/降级链路生效。
      clearOpenTimer();
      openTimer = setTimeout(() => {
        if (ws.readyState !== WebSocket.OPEN) ws.close();
      }, 6000);
      // 半死连接防御（2026-09-01 实测：后端 pong 与推送并发写曾致 writer 静默死亡，
      // 连接 open、ping 有应答、但推送为零——列表冻结在初始值。后端已改为单点发送；
      // 这里再加客户端自愈：32s（2 个心跳周期）内没收到任何消息就主动断开重连，
      // 重连失败 3 次自然落入 REST 轮询兜底）
      let lastMsgAt = Date.now();
      ws.onopen = () => {
        clearOpenTimer();
        retry = 0;
        stopPolling();
        lastMsgAt = Date.now();
        setSentKey(""); // 新 socket 允许下一次 key 变化时重发订阅
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
        clearOpenTimer();
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
      clearOpenTimer();
      clearThrottle();
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
  // 去重键用 state（sentKey）：ref 跨 effect 写入触发 react-hooks 编译规则
  // 报 error（"This value cannot be modified"），且发送后的一次重渲染无副作用。
  useEffect(() => {
    const ws = wsRef.current;
    if (!connectedRef.current || !ws || ws.readyState !== WebSocket.OPEN) return;
    if (!key || key === sentKey) return;
    setSentKey(key);
    try {
      ws.send(JSON.stringify({ action: "subscribe", symbols: [...new Set(symbolsRef.current)] }));
    } catch {}
  }, [key, sentKey]);

  return { quotes, status };
}
