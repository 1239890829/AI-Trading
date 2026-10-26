"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { getQuotes, wsBase } from "@/lib/api";
import { wsSubprotocols } from "@/lib/ws-credential";
import type { Quote } from "@/types/market";

/** Communication only. Quote quality and trading phase belong to each Quote. */
export type StreamStatus = "connecting" | "live" | "polling" | "error";

/** One stream owner's immediate, symbol-scoped view. null means a received
 * frame omitted that subscribed symbol; undefined means no observation yet. */
export interface QuoteFeed {
  subscribe(symbol: string, listener: () => void): () => void;
  getSnapshot(symbol: string): Quote | null | undefined;
}

function createQuoteFeed(symbols: string[]) {
  let selected = new Set(symbols);
  let current = new Map<string, Quote | null>();
  const listeners = new Map<string, Set<() => void>>();
  const replace = (next: Map<string, Quote | null>) => {
    const previous = current;
    current = next;
    for (const [symbol, group] of listeners) {
      if (previous.get(symbol) !== next.get(symbol)) group.forEach(listener => listener());
    }
  };
  const feed: QuoteFeed = {
    subscribe(symbol, listener) {
      const group = listeners.get(symbol) ?? new Set<() => void>();
      group.add(listener);
      listeners.set(symbol, group);
      return () => {group.delete(listener); if (!group.size) listeners.delete(symbol);};
    },
    getSnapshot: symbol => current.get(symbol),
  };
  return {
    feed,
    setSymbols(nextSymbols: string[]) {
      selected = new Set(nextSymbols);
      replace(new Map([...current].filter(([symbol]) => selected.has(symbol))));
    },
    publish(list: Quote[]) {
      const next = new Map<string, Quote | null>([...selected].map(symbol => [symbol, null]));
      for (const quote of list) if (selected.has(quote.symbol)) next.set(quote.symbol, quote);
      replace(next);
    },
    clear: () => replace(new Map()),
    dispose() {replace(new Map()); listeners.clear();},
  };
}

/** 连接状态 → 界面文案（共享：工作台与个股详情同一套，避免两处文案漂移）。 */
export const STREAM_STATUS_LABEL: Record<StreamStatus, { text: string; cls: string; title: string }> = {
  connecting: { text: "连接中", cls: "text-zinc-600 dark:text-zinc-400", title: "正在建立WebSocket行情通道或等待第一帧；不代表已有新报价" },
  live: { text: "已连接", cls: "text-zinc-600 dark:text-zinc-400", title: "WebSocket行情通道已收到市场帧；不代表源报价已更新" },
  polling: { text: "备用读取", cls: "text-amber-800 dark:text-amber-400", title: "WebSocket尚未恢复，使用REST备用通道；源报价时效仍须单独核对" },
  error: { text: "备用读取失败", cls: "text-red-700 dark:text-red-400", title: "REST备用读取失败，正在自动重试；保留已有报价，不声明时效已恢复" },
};

/** Read quality never changes this transport label. */
export const STREAM_ABNORMAL: readonly StreamStatus[] = ["connecting", "polling", "error"];

/**
 * 行情流：优先 WebSocket（/ws/quotes），断线自动重连；
 * 连续失败 3 次后降级为 REST 轮询（请求结束后间隔 5s），并在恢复时切回 WS。
 *
 * 订阅更新（2026-09-01 架构方案 P1，修复实锤断点 P2）：后端支持
 * {"action":"subscribe"} 动态切换订阅集——自选集合变化时发送 subscribe 消息
 * 而非整条重连。此前 symbols 每次变化 → effect 重建 → WS 重连，产生行情空窗，
 * 重连失败 3 次还会误入降级轮询。
 */
export function useQuoteStream(symbols: string[], opts?: { throttleMs?: number }) {
  const [owner] = useState(() => createQuoteFeed(symbols));
  useEffect(() => () => owner.dispose(), [owner]);
  // Keep explicit missing observations on the same throttled snapshot as prices.
  // A bootstrap consumer must distinguish them from no frame received yet.
  const [observations, setObservations] = useState<Record<string, Quote | null>>({});
  const quotes = useMemo<Record<string, Quote>>(() => Object.fromEntries(
    Object.entries(observations).filter((entry): entry is [string, Quote] => entry[1] !== null),
  ), [observations]);
  const [status, setStatus] = useState<StreamStatus>("connecting");
  // 已发送的订阅 key（去重用，见下方订阅 effect 注释）
  const [sentKey, setSentKey] = useState("");
  // 展示节流（2026-09-02 用户反馈：自选列表 1Hz 刷新频繁闪烁）：
  // WS 仍按 1Hz 全量接收（内部状态不丢数据），对外 quotes 状态按
  // throttleMs 节流应用（trailing——间隔内的最后一条生效）。选中详情通过
  // quoteFeed 即时消费同一连接，列表不跟着逐秒重渲染。首帧不节流。
  // status 不节流：连接态变化必须即时呈现。不传 throttleMs 行为不变。
  const throttleMs = opts?.throttleMs ?? 0;

  const key = [...symbols].sort().join(",");
  const hasSymbols = key.length > 0;

  // symbols 的实时值供重连/订阅使用（effect 闭包不可靠，渲染期写 ref 在并发渲染下同样不可靠）
  const symbolsRef = useRef<string[]>(symbols);
  const refreshSelectionRef = useRef<(() => void) | null>(null);
  useEffect(() => {
    symbolsRef.current = key ? key.split(",") : [];
    owner.setSymbols(symbolsRef.current);
    refreshSelectionRef.current?.();
  }, [key, owner]);

  const wsRef = useRef<WebSocket | null>(null);
  const connectedRef = useRef(false);
  const subscriptionRef = useRef<{socket: WebSocket; id: number; key: string} | null>(null);

  useEffect(() => {
    if (!hasSymbols || connectedRef.current) return;
    connectedRef.current = true;

    let closed = false;
    let retry = 0;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let pollTimer: ReturnType<typeof setTimeout> | null = null;
    let pingTimer: ReturnType<typeof setInterval> | null = null;
    let openTimer: ReturnType<typeof setTimeout> | null = null;
    let throttleTimer: ReturnType<typeof setTimeout> | null = null;
    let lastAppliedAt = 0;
    let pending: Quote[] | null = null;
    // QuoteHub 的 snapshot/quotes/stale 都是整个订阅集的快照，不是变更增量。
    // 隐藏时继续接收真实 WS 帧和心跳，只暂缓 React/feed 通知。
    // 保存完整 Quote，不能仅按价格去重而丢掉源钟、质量、成交量或金额。
    let latestFrame: Quote[] | null = null;
    let deferredStatus: StreamStatus | null = null;
    const hidden = () => document.visibilityState === "hidden";
    const clearOpenTimer = () => {
      if (openTimer) clearTimeout(openTimer);
      openTimer = null;
    };
    const clearThrottle = () => {
      if (throttleTimer) clearTimeout(throttleTimer);
      throttleTimer = null;
    };
    const applyNow = (list: Quote[]) => {
      if (closed || hidden()) return;
      const selected = new Set(symbolsRef.current);
      const current = list.filter(quote => selected.has(quote.symbol));
      if (list.length && !current.length) return;
      lastAppliedAt = Date.now();
      pending = null;
      const snapshot: Record<string, Quote | null> = Object.fromEntries([...selected].map(symbol => [symbol, null]));
      for (const quote of current) snapshot[quote.symbol] = quote;
      setObservations(snapshot);
    };
    const apply = (list: Quote[]) => {
      if (closed) return;
      const selected = new Set(symbolsRef.current);
      const current = list.filter(quote => selected.has(quote.symbol));
      if (list.length && !current.length) return;
      latestFrame = current;
      if (hidden()) return;
      owner.publish(current);
      if (throttleMs <= 0) {
        applyNow(current);
        return;
      }
      pending = current;
      const elapsed = Date.now() - lastAppliedAt;
      if (elapsed >= throttleMs) {
        if (throttleTimer) clearTimeout(throttleTimer);
        throttleTimer = null;
        applyNow(current);
        return;
      }
      if (throttleTimer) return; // 已有待触发的 trailing 应用
      throttleTimer = setTimeout(() => {
        throttleTimer = null;
        if (closed || !pending) return;
        applyNow(pending);
      }, throttleMs - elapsed);
    };

    // A fallback request owns one completion-based timer. Visibility, subscription
    // and WS recovery invalidate results, while an outstanding HTTP request drains.
    let polling = false;
    let pollGeneration = 0;
    let pollInFlight: Promise<void> | null = null;
    let pollAgain = false;
    const clearPollTimer = () => {
      if (pollTimer !== null) clearTimeout(pollTimer);
      pollTimer = null;
    };
    const tick = (): Promise<void> => {
      if (closed || !polling || hidden() || !symbolsRef.current.length) return Promise.resolve();
      if (pollInFlight) {
        pollAgain = true;
        return pollInFlight;
      }
      const generation = pollGeneration;
      const requestedSymbols = [...symbolsRef.current];
      const current = () => !closed && polling && !hidden()
        && generation === pollGeneration;
      pollInFlight = (async () => {
        try {
          const list = await getQuotes(requestedSymbols);
          if (current()) {
            apply(list);
            setStatus("polling");
          }
        } catch {
          if (current()) setStatus("error");
        } finally {
          pollInFlight = null;
          if (!closed && polling && !hidden()) {
            clearPollTimer();
            if (pollAgain) {
              pollAgain = false;
              void tick();
            } else {
              pollTimer = setTimeout(() => { pollTimer = null; void tick(); }, 5000);
            }
          }
        }
      })();
      return pollInFlight;
    };
    const restartPolling = () => {
      pollGeneration += 1;
      clearPollTimer();
      if (!closed && polling && !hidden()) void tick();
    };
    const startPolling = () => {
      if (polling || closed) return;
      polling = true;
      setStatus("polling");
      void tick();
    };
    const stopPolling = () => {
      polling = false;
      pollGeneration += 1;
      pollAgain = false;
      clearPollTimer();
      clearThrottle();
      pending = null;
    };
    refreshSelectionRef.current = () => {
      clearThrottle();
      pending = null;
      latestFrame = null;
      deferredStatus = null;
      restartPolling();
    };
    const onVisibility = () => {
      if (closed) return;
      clearThrottle();
      pending = null;
      if (polling) {
        restartPolling();
      } else if (!hidden()) {
        // 一次校准直接采用隐藏期最后一帧，不等待列表节流或另发重复 REST。
        if (latestFrame !== null) {
          owner.publish(latestFrame);
          applyNow(latestFrame);
        }
        if (deferredStatus !== null) setStatus(deferredStatus);
        deferredStatus = null;
      }
    };
    document.addEventListener("visibilitychange", onVisibility);

    const connect = async () => {
      if (closed) return;
      // 重连时保持当前状态显示（不闪回 connecting 误导用户）
      setStatus((prev) => (retry === 0 ? "connecting" : prev));
      // R22：/ws/quotes 在启用鉴权后要求**子协议**携带凭据（浏览器不允许给
      // WebSocket 设自定义请求头，而 ?token= 通道已刻意关闭）。凭据由同源
      // 每次连接取得短效配对票据；凭据不可用时沿既有重试路径等待。
      const protocols = await wsSubprotocols();
      // await 期间可能已卸载/断开：这里必须重新判一次，否则会创建一个
      // 无人回收的 socket（cleanup 已经跑过了）。
      if (closed) return;
      if (!protocols.length) {
        retry += 1;
        if (retry >= 3) startPolling();
        reconnectTimer = setTimeout(() => void connect(), Math.min(1000 * 2 ** retry, 10000));
        return;
      }
      let ws: WebSocket;
      const openingKey = [...symbolsRef.current].sort().join(",");
      try {
        const url = `${wsBase()}/ws/quotes?symbols=${symbolsRef.current.join(",")}&subscription_id=0`;
        ws = new WebSocket(url, protocols);
        wsRef.current = ws;
        subscriptionRef.current = {socket: ws, id: 0, key: openingKey};
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
      // 这里再加客户端自愈：32s（2 个心跳周期）内没有合法行情帧就断开重连，pong 不续命；
      // 重连失败 3 次自然落入 REST 轮询兜底）
      let lastFrameAt = Date.now();
      let receivedMarketFrame = false;
      ws.onopen = () => {
        if (closed || wsRef.current !== ws) return;
        clearOpenTimer();
        lastFrameAt = Date.now();
        // The URL already subscribed this set. A set changed while connecting
        // is reconciled by the existing subscription effect, on this socket.
        setSentKey(openingKey);
        // A handshake alone neither proves market delivery nor recovers REST.
        pingTimer = setInterval(() => {
          if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
            if (Date.now() - lastFrameAt > 32_000) {
              wsRef.current.close(); // 触发 onclose 重连
              return;
            }
            wsRef.current.send(JSON.stringify({ action: "ping" }));
          }
        }, 15000);
      };
      ws.onmessage = (ev) => {
        if (closed || wsRef.current !== ws) return;
        try {
          const msg = JSON.parse(ev.data as string) as { type: string; data?: Quote[]; subscription_id?: unknown };
          // All three frames prove communication, including an explicit empty
          // full snapshot. Per-symbol stale/closed/missing facts stay in the feed.
          if ((msg.type === "snapshot" || msg.type === "quotes" || msg.type === "stale") && Array.isArray(msg.data)) {
            const subscription = subscriptionRef.current;
            if (!subscription || subscription.socket !== ws || subscription.key !== symbolsRef.current.join(",")) return;
            // Legacy servers are authoritative only for this socket's initial
            // URL set. After a subscribe, an unscoped frame cannot prove missing
            // symbols or renew the market watchdog; normal reconnect/REST recovers.
            const legacyInitial = msg.subscription_id === undefined && subscription.id === 0;
            if (!legacyInitial && (!Number.isInteger(msg.subscription_id) || msg.subscription_id !== subscription.id)) return;
            const selected = new Set(symbolsRef.current);
            const currentData = msg.data.filter(quote => selected.has(quote.symbol));
            if (msg.data.length && !currentData.length) return;
            lastFrameAt = Date.now();
            if (!receivedMarketFrame) {
              receivedMarketFrame = true;
              retry = 0;
              stopPolling();
            }
            apply(currentData);
            if (hidden()) deferredStatus = "live";
            else {deferredStatus = null; setStatus("live");}
          }
        } catch {}
      };
      ws.onclose = () => {
        if (closed || wsRef.current !== ws) return;
        wsRef.current = null;
        clearOpenTimer();
        if (pingTimer) clearInterval(pingTimer);
        pingTimer = null;
        if (closed) return;
        retry += 1;
        if (retry >= 3) startPolling();
        else if (!polling) {
          if (hidden()) deferredStatus = "connecting";
          else setStatus("connecting");
        }
        reconnectTimer = setTimeout(() => void connect(), Math.min(1000 * 2 ** retry, 10000));
      };
      ws.onerror = () => {
        if (!closed && wsRef.current === ws) ws.close();
      };
    };

    void connect();

    return () => {
      closed = true;
      refreshSelectionRef.current = null;
      connectedRef.current = false;
      clearOpenTimer();
      clearThrottle();
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (pingTimer) clearInterval(pingTimer);
      document.removeEventListener("visibilitychange", onVisibility);
      stopPolling();
      owner.clear();
      wsRef.current?.close();
      wsRef.current = null;
      subscriptionRef.current = null;
    };
    // throttleMs 是**数字**（`opts?.throttleMs ?? 0`），依赖按值比较——
    // 调用方每次传内联对象也不会导致重连；真改动节流值时才重连，符合预期。
  }, [hasSymbols, throttleMs, owner]);

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
    const subscription = subscriptionRef.current;
    if (!subscription || subscription.socket !== ws) return;
    if (subscription.id >= 2 ** 31 - 1) {ws.close(); return;}
    const nextId = subscription.id + 1;
    subscriptionRef.current = {socket: ws, id: nextId, key};
    // setSentKey 必须留在 effect 同步路径：它与下方 ws.send 是同一次订阅切换的
    // 原子操作（渲染期做会把网络发送放进渲染、拆散两步的原子性）。
    setSentKey(key);
    try {
      ws.send(JSON.stringify({ action: "subscribe", symbols: [...new Set(symbolsRef.current)], subscription_id: nextId }));
    } catch {ws.close();}
  }, [key, sentKey]);

  return { quotes, observations, status, quoteFeed: owner.feed };
}
