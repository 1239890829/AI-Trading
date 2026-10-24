"use client";

import {useCallback, useEffect, useRef, useState} from "react";
import {createPortal} from "react-dom";
import type {NotificationItem} from "@/lib/api";
import {dateTimeTextBJ, fmt} from "@/lib/format";
import {isCleared} from "@/lib/notification-read";
import {
  confirmArrivalDisplay, notificationArrivals, reconcileArrivalLedger, reserveArrival,
  subscribeArrivalLedger, updateArrivalLedger, type ArrivalNotification, type NotificationArrival,
} from "@/lib/notification-arrivals";
import {notificationSummary} from "./notification-detail";
import styles from "./notification-arrival-stack.module.css";

function ArrivalIcon({kind}: {kind: NotificationArrival["kind"]}) {
  const shape = kind === "selection" ? <><path d="m5 12 4 4L19 6"/><path d="M19 12v7H5V5h9"/></>
    : kind === "confirmation" ? <><path d="m4 15 5-5 4 3 7-8"/><path d="M15 5h5v5"/></>
    : kind === "research_ready" ? <><path d="M5 4h9l5 5v11H5z"/><path d="M14 4v6h5M8 14h8M8 17h5"/></>
    : kind === "paper_status" ? <><path d="M4 5h16v15H4zM4 10h16M9 5v15"/><path d="m12 15 2 2 3-4"/></>
    : <><path d="m12 3 10 18H2z"/><path d="M12 9v5m0 3v1"/></>;
  return <svg aria-hidden="true" width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">{shape}</svg>;
}

const evidenceObject=(value:unknown):Record<string,unknown>=>value && typeof value==="object" && !Array.isArray(value) ? value as Record<string,unknown> : {};
const actualNumber=(value:unknown):value is number=>typeof value==="number" && Number.isFinite(value);

/** A progress card consumes its own exact-version receipt, never the anchor's old selection prose. */
export function notificationArrivalSummary(arrival:NotificationArrival):string {
  if (arrival.kind!=="research_ready" && arrival.kind!=="paper_status") return notificationSummary(arrival.item);
  const reference=arrival.item.references?.find(ref=>ref.kind===arrival.kind && ref.source_version===arrival.version);
  if (!reference) return "进展已更新，请核对原记录";
  const evidence=evidenceObject(reference.evidence);
  if (arrival.kind==="research_ready") {
    const research=evidenceObject(evidence.research);
    return research.report_count===7 && typeof research.run_id==="string" && research.run_id.length>0
      ? "七份报告已保存，可逐角色核对；仍待核" : "进展已更新，请核对原记录";
  }
  const paper=evidenceObject(evidence.paper_execution);
  if (paper.scope!=="main") return "进展已更新，请核对原记录";
  const reason=typeof paper.reason==="string" && paper.reason.trim() ? ` ${paper.reason.trim()}` : "";
  if (paper.status==="pending") return `模拟委托待成交。${reason}`;
  if (paper.status==="rejected") return `模拟委托已拒绝。${reason}`;
  if (paper.status==="cancelled") return `模拟委托已取消。${reason}`;
  if (paper.status==="filled") {
    if (actualNumber(paper.filled_price) && paper.filled_price>0 && actualNumber(paper.quantity)
      && Number.isSafeInteger(paper.quantity) && paper.quantity>0 && actualNumber(paper.fee) && paper.fee>=0
      && actualNumber(paper.order_id) && Number.isSafeInteger(paper.order_id) && paper.order_id>0) {
      return `模拟已成交：${paper.quantity}股 · ${fmt(paper.filled_price)}元 · 费用${fmt(paper.fee)}元`;
    }
    return "模拟成交资料未齐，请核对成交价、数量和费用。";
  }
  return `模拟结果未知，请核对原委托；未当作成交。${reason}`;
}

/** One display consumer of Bell's existing read. A shown/dismissed card is not a read receipt. */
export function NotificationArrivalStack({items, ready, clearBefore, onOpen, onOpenCenter, suspended = false}: {
  items: readonly ArrivalNotification[]; ready: boolean; clearBefore: number; suspended?: boolean;
  onOpen: (item: NotificationItem) => void; onOpenCenter: () => void;
}) {
  const [visible, setVisible] = useState<NotificationArrival[]>([]);
  const [waiting, setWaiting] = useState(0);
  const [deferred, setDeferred] = useState(0);
  const [active, setActive] = useState(false);
  const owner = useRef("");
  const stackRef = useRef<HTMLElement>(null);
  const visibleRef = useRef<NotificationArrival[]>([]);
  const current = useRef({items, ready, clearBefore, suspended});
  const limit = useRef(3);
  const busy = useRef(false);
  const requested = useRef(false);
  const mounted = useRef(false);
  const pump = useCallback(async () => {
    if (busy.current) {requested.current = true; return;}
    if (!mounted.current || !current.current.ready || document.hidden || current.current.suspended) return;
    busy.current = true;
    try {
      do {
        requested.current = false;
        if (!mounted.current || !current.current.ready || document.hidden || current.current.suspended) break;
        const candidates = notificationArrivals(current.current.items.filter(item => !isCleared(item, current.current.clearBefore)));
        const byKey = new Map(candidates.map(arrival => [arrival.key, arrival]));
        let selected: NotificationArrival[] = [];
        let retained: NotificationArrival[] = [];
        let pending = 0;
        let overflow = 0;
        await updateArrivalLedger(previous => {
          let ledger = reconcileArrivalLedger(previous, candidates, Date.now());
          const entries = new Map(ledger.entries.map(entry => [entry.key, entry]));
          retained = visibleRef.current.filter(arrival => {
            const entry = entries.get(arrival.key);
            return entry?.owner === owner.current && (entry.state === "claimed" || entry.state === "displayed")
              && !isCleared(arrival.item, current.current.clearBefore);
          }).slice(0, limit.current);
          const retainedKeys = new Set(retained.map(arrival => arrival.key));
          for (const entry of ledger.entries.filter(entry => entry.state === "pending").sort((a, b) => a.at - b.at)) {
            if (retained.length + selected.length >= limit.current) break;
            const arrival = byKey.get(entry.key);
            if (arrival && !retainedKeys.has(arrival.key)) {
              ledger = reserveArrival(ledger, entry.key, owner.current, Date.now());
              selected.push(arrival);
            }
          }
          pending = ledger.entries.filter(entry => entry.state === "pending" && byKey.has(entry.key)).length;
          overflow = ledger.entries.filter(entry => entry.state === "deferred" && byKey.has(entry.key)).length;
          return ledger;
        });
        if (!mounted.current || document.hidden || !current.current.ready || current.current.suspended) break;
        const next = [...retained, ...selected];
        visibleRef.current = next;
        setVisible(previous => previous.length === next.length && previous.every((arrival, i) => arrival.key === next[i].key) ? previous : next);
        setWaiting(pending); setDeferred(overflow);
      } while (requested.current);
    } finally {busy.current = false;}
  }, []);

  useEffect(() => {
    mounted.current = true;
    owner.current = typeof crypto.randomUUID === "function" ? crypto.randomUUID() : `tab-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    const media = typeof window.matchMedia === "function" ? window.matchMedia("(max-width: 639px)") : null;
    const changed = () => {
      limit.current = media?.matches ? 1 : 3;
      setActive(!document.hidden);
      void pump();
    };
    changed();
    media?.addEventListener("change", changed);
    document.addEventListener("visibilitychange", changed);
    const unsubscribe = subscribeArrivalLedger(() => {void pump();});
    return () => {mounted.current = false; unsubscribe(); media?.removeEventListener("change", changed); document.removeEventListener("visibilitychange", changed);};
  }, [pump]);
  useEffect(() => {current.current = {items, ready, clearBefore, suspended}; void pump();}, [items, ready, clearBefore, suspended, pump]);
  useEffect(() => {
    if (!active || !ready || suspended || !visible.length) return;
    // This effect runs after the cards commit. A queue reservation alone is not display evidence.
    void updateArrivalLedger(ledger => confirmArrivalDisplay(ledger, visible.map(arrival => arrival.key), owner.current, Date.now()));
  }, [visible, active, ready, suspended]);

  const dismiss = (key: string, restoreFocus = false) => {
    visibleRef.current = visibleRef.current.filter(arrival => arrival.key !== key);
    setVisible(visibleRef.current);
    void pump();
    if (restoreFocus) queueMicrotask(() => {
      const next = stackRef.current?.querySelector<HTMLButtonElement>("[data-arrival-read]")
        ?? document.querySelector<HTMLButtonElement>(".notification-trigger");
      next?.focus({preventScroll:true});
    });
  };
  const openCenter = () => {
    // Hand the bounded backlog to the existing list; this does not read its records.
    const keys = new Set(notificationArrivals(current.current.items).map(arrival => arrival.key));
    void updateArrivalLedger(ledger => ({...ledger, entries: ledger.entries.map(entry => keys.has(entry.key) && (entry.state === "pending" || entry.state === "deferred")
      ? {...entry, state:"dismissed"} : entry)}));
    onOpenCenter();
  };
  if (!active || suspended || (!visible.length && !waiting && !deferred)) return null;
  return createPortal(<aside ref={stackRef} aria-label="新到提醒" className={styles.stack} data-testid="notification-arrivals">
    <div className={styles.announcement} role="status" aria-live="polite" aria-atomic="true">{visible.length ? visible.map(arrival => `${arrival.label}：${arrival.item.title}。`).join(" ") : "还有提醒保留在通知中心。"}</div>
    {visible.map(arrival => <article key={arrival.key} className={styles.card} data-kind={arrival.kind} data-testid="notification-arrival" onKeyDown={event => {
      if (event.key === "Escape" && !event.nativeEvent.isComposing && event.keyCode !== 229) {
        event.preventDefault(); event.stopPropagation(); dismiss(arrival.key, true);
      }
    }}>
      <span className={styles.icon}><ArrivalIcon kind={arrival.kind}/></span>
      <button type="button" data-arrival-read className={styles.read} aria-label={`${arrival.label}：${arrival.item.title}，查看原记录`} onClick={() => {dismiss(arrival.key); onOpen(arrival.item);}}>
        <span className={styles.meta}><strong>{arrival.label}</strong><span>{arrival.source}</span><time>{dateTimeTextBJ(new Date(arrival.at).toISOString())}</time></span>
        <span className={styles.title}>{arrival.item.title.replace(/^【[^】]+】\s*/, "")}</span>
        <span className={styles.summary}>{notificationArrivalSummary(arrival)}</span>
        <span className={styles.action}>查看原记录 <span aria-hidden="true">↗</span></span>
      </button>
      <button type="button" className={styles.close} aria-label={`收起${arrival.label}提醒：${arrival.item.title}`} title="仅收起提醒，仍保留未读" onClick={event => dismiss(arrival.key, event.detail === 0)}><span aria-hidden="true">×</span></button>
    </article>)}
    {(waiting > 0 || deferred > 0) && <button type="button" className={styles.more} onClick={openCenter}>{waiting > 0 ? `另有 ${waiting} 条排队` : "更多提醒"}{deferred > 0 ? ` · ${deferred} 条已保留` : ""}<span>通知中心 →</span></button>}
  </aside>, document.body);
}
