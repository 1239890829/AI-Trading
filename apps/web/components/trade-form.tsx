"use client";

import { useEffect, useState } from "react";
import { fmt, parseNum } from "@/lib/format";
import { checkOrderRisk, placePaperOrder } from "@/lib/api";
import type { OrderCheckResult } from "@/lib/api";

/** 模拟交易下单表单：买/卖切换、价格默认现价、数量、预估金额与费用、涨跌停提示。 */
export function TradeForm({
  symbol,
  price,
  limitUp,
  limitDown,
  onTraded,
}: {
  symbol: string;
  price: number | null;
  limitUp?: number | null;
  limitDown?: number | null;
  /** 下单成功（成交或挂单）后回调——父组件刷新模拟账户数据（2026-09-01：替代全局事件）。 */
  onTraded?: () => void;
}) {
  const [side, setSide] = useState<"buy" | "sell">("buy");
  const [p, setP] = useState("");
  const [qty, setQty] = useState("100");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [riskCheck, setRiskCheck] = useState<OrderCheckResult | null>(null);
  const [checking, setChecking] = useState(false);

  // 详情页传入的现价 → 表单价回填（渲染期 adjust-state：价格/标的任一变化即重填，
  // 语义与原 effect 依赖 [price, symbol] 一致）
  const [prevPriceKey, setPrevPriceKey] = useState<string | null>(null);
  const priceKey = price ? `${symbol}:${price}` : null;
  if (priceKey !== prevPriceKey) {
    setPrevPriceKey(priceKey);
    if (price) setP(fmt(price));
  }

  const pv = parseNum(p);
  const qv = Math.trunc(parseNum(qty));
  const est = pv * qv;
  const fee = Math.max(est * 0.00025, 5) + (side === "sell" ? est * 0.0005 : 0);

  // 输入无效 → 渲染期同步清掉旧检查结果（adjust-state 模式）
  if (pv <= 0 || qv <= 0) {
    if (riskCheck !== null) setRiskCheck(null);
  }
  useEffect(() => {
    if (pv <= 0 || qv <= 0) return;
    const t = setTimeout(() => {
      setChecking(true);
      void checkOrderRisk({ symbol, side, price: pv, quantity: qv })
        .then(setRiskCheck)
        .catch(() => setRiskCheck(null))
        .finally(() => setChecking(false));
    }, 300);
    return () => clearTimeout(t);
  }, [symbol, side, pv, qv]);

  async function submit() {
    setSubmitting(true);
    setMsg(null);
    try {
      // 风控预检：未通过则阻止下单
      if (!riskCheck || !riskCheck.allowed) {
        setMsg({
          ok: false,
          text: riskCheck?.reasons[0] ?? "风控预检未通过",
        });
        setSubmitting(false);
        return;
      }
      const r = await placePaperOrder(symbol, side, pv, qv);
      setMsg({
        ok: true,
        text: r.status === "filled" ? `已成交 @ ${fmt(r.filled_price)}（费 ${fmt(r.fee)}）` : "已挂单，等待撮合",
      });
      onTraded?.();
    } catch (e) {
      setMsg({ ok: false, text: (e as Error).message });
    } finally {
      setSubmitting(false);
    }
  }

  const bad =
    pv <= 0 ||
    qv <= 0 ||
    (side === "buy" && qv % 100 !== 0) ||
    (limitUp != null && side === "buy" && pv >= limitUp) ||
    (limitDown != null && side === "sell" && pv <= limitDown) ||
    (riskCheck?.allowed === false);

  return (
    <div className="shrink-0 border-b border-zinc-100 px-3 py-2 dark:border-zinc-800/60">
      <div className="mb-2 flex gap-1">
        {(["buy", "sell"] as const).map((sd) => (
          <button
            key={sd}
            onClick={() => setSide(sd)}
            className={`flex-1 rounded py-1 text-sm font-medium ${
              side === sd ? (sd === "buy" ? "bg-up text-white" : "bg-down text-white") : "bg-zinc-100 text-zinc-400 dark:bg-zinc-800"
            }`}
          >
            {sd === "buy" ? "买入" : "卖出"}
          </button>
        ))}
      </div>
      <div className="space-y-1.5 text-xs">
        <label className="flex items-center justify-between gap-2">
          <span className="text-zinc-400">价格</span>
          <input
            value={p}
            onChange={(e) => setP(e.target.value)}
            inputMode="decimal"
            className="w-28 rounded border border-zinc-200 bg-transparent px-2 py-1 text-right font-mono text-zinc-900 outline-none focus:border-up/60 dark:border-zinc-700 dark:text-zinc-100"
          />
        </label>
        <label className="flex items-center justify-between gap-2">
          <span className="text-zinc-400">数量</span>
          <input
            value={qty}
            onChange={(e) => setQty(e.target.value.replace(/[^0-9]/g, ""))}
            inputMode="numeric"
            className="w-28 rounded border border-zinc-200 bg-transparent px-2 py-1 text-right font-mono text-zinc-900 outline-none focus:border-up/60 dark:border-zinc-700 dark:text-zinc-100"
          />
        </label>
        <div className="flex justify-between text-zinc-500">
          <span>预估金额</span>
          <span className="font-mono">{fmt(est)} + 费 {fmt(fee)}</span>
        </div>
        {riskCheck && (
          <div className="flex justify-between text-zinc-500">
            <span>{side === "buy" ? "风控可买上限" : "可卖（T+1）"}</span>
            <span className="font-mono">{fmt(riskCheck.max_qty, 0)} 股</span>
          </div>
        )}
        {limitUp != null && side === "buy" && (
          <div className="flex justify-between text-zinc-500">
            <span>涨停价</span>
            <span className="font-mono">{fmt(limitUp)}（≥则拒单）</span>
          </div>
        )}
        {limitDown != null && side === "sell" && (
          <div className="flex justify-between text-zinc-500">
            <span>跌停价</span>
            <span className="font-mono">{fmt(limitDown)}（≤则拒单）</span>
          </div>
        )}
      </div>
      <button
        onClick={() => void submit()}
        disabled={submitting || bad}
        className={`mt-2 w-full rounded py-1.5 text-sm font-medium text-white disabled:opacity-40 ${side === "buy" ? "bg-up" : "bg-down"}`}
      >
        {submitting ? "提交中…" : `${side === "buy" ? "买入" : "卖出"} ${symbol}`}
      </button>
      {msg && <p className={`mt-1.5 text-xs ${msg.ok ? "text-emerald-400" : "text-red-400"}`}>{msg.text}</p>}
      {checking && !riskCheck && <p className="mt-1 text-[11px] text-zinc-500">风控预检中…</p>}
      {riskCheck && riskCheck.warnings.length > 0 && (
        <p className="mt-1 text-[11px] text-amber-400">⚠ {riskCheck.warnings.join("；")}</p>
      )}
      {riskCheck?.allowed === false && (
        <p className="mt-1 text-[11px] text-red-400">⛔ {riskCheck.reasons.join("；")}</p>
      )}
      {bad && pv > 0 && !submitting && (
        <p className="mt-1 text-[11px] text-zinc-500">
          {side === "buy" && qv % 100 !== 0 ? "买入须为 100 股整数倍 " : ""}
          {limitUp != null && side === "buy" && pv >= limitUp ? "价格已达涨停，将被拒绝 " : ""}
          {limitDown != null && side === "sell" && pv <= limitDown ? "价格已达跌停，将被拒绝" : ""}
        </p>
      )}
    </div>
  );
}
