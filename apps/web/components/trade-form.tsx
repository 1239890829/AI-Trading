"use client";

import { useEffect, useRef, useState } from "react";
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
  const [checking, setChecking] = useState(false);

  /** 风控预检结果 + 它的订单签名（R19）。
   *
   * 只存结果是不够的：预检是异步的，用户改数量/方向会发新请求，**旧回包可能后到**
   * 并覆盖新结论。实测路径：1000 股预检已判"拒绝"，随后到达的**100 股**旧"允许"
   * 回包把按钮重新点亮，看起来 1000 股被测过且通过了。
   * 故结果必须与它测的那张订单绑定：`sig` 是 `symbol|side|price|qty`，读取端只在
   * 签名与当前草稿一致时才认这份结果（失配 = 过期，等同"没检查过"），
   * 提交也只接受一致签名 —— 绝不拿 A 单的结论给 B 单放行。 */
  const [riskCheck, setRiskCheck] = useState<{ sig: string; result: OrderCheckResult } | null>(null);

  /** 手填价草稿是否已被用户接管（R19）。
   *
   * 缺陷：原实现用 `symbol:price` 作重填 key，**每一个行情 tick 都算"变了"** ⇒
   * 每轮把 `setP(fmt(price))` 重跑一遍，用户手填的 9.50 被下一拍现价 10.10 冲掉，
   * 且没有任何提示。现语义：现价只在**首次 / 换标的**时填默认值，用户一旦接管即
   * 锁定草稿，要回到现价请显式点「使用现价」——外部现价与用户意图彻底分离。
   *
   * ⚠️ 为什么用 `onFocus` 而不仅靠 `onChange`：React 对**值未变化**的 change 事件
   * 做了去重（内部 value tracker 相同即不派发 onChange）。而"用户手填的值恰好等于
   * 当前现价"是最常见的一种输入（照着现价敲一遍）——只认 onChange 时它**不算编辑**，
   * 下一拍 tick 照样冲掉，缺陷原样复现。"进入该输入框"才是可靠的用户意图信号。 */
  const [priceEdited, setPriceEdited] = useState(false);
  const [draftSymbol, setDraftSymbol] = useState(symbol);

  if (draftSymbol !== symbol) {
    // 换标的：草稿作废，重新以新标的现价起手
    setDraftSymbol(symbol);
    setPriceEdited(false);
    if (price != null) setP(fmt(price));
  } else if (!priceEdited && price != null) {
    // 未编辑过：跟随现价刷新（首帧回填也走这条路）
    const next = fmt(price);
    if (next !== p) setP(next);
  }

  const pv = parseNum(p);
  const qv = Math.trunc(parseNum(qty));
  const est = pv * qv;
  const fee = Math.max(est * 0.00025, 5) + (side === "sell" ? est * 0.0005 : 0);

  /** 当前草稿的订单签名。任何一项变化即失效（含价格、方向、数量、标的）。 */
  const orderSig = `${symbol}|${side}|${pv}|${qv}`;
  /** 与当前草稿签名一致的预检结果；失配一律视为"尚无有效预检"。 */
  const active = riskCheck && riskCheck.sig === orderSig ? riskCheck.result : null;

  /** 最近一次**已发出**的预检请求的签名。
   *
   * 为什么需要它：**回包在接受时就要按"是否仍是当前订单"过滤**，只在渲染期比对
   * 是不够的——若让过期回包写进 state，它会把更新的结论**冲成"无结论"**：
   * 实测路径为「1000 股判拒（按钮禁用）→ 100 股旧'允许'回包到达 → 结果被覆盖成
   * 签名失配 → active=null → 按钮重新点亮」。故过期回包**直接丢弃，不入 state**。
   * 两道防线各司其职：ref 拦"过期写入"，渲染期 sig 比对拦"草稿又变了"。
   *
   * ⚠️ 写入位置在 **effect 体内首行**，不在渲染期（R19 修复）：渲染期写
   * `ref.current` 会被 `react-hooks/refs` 判为 error（React 官方规则：渲染必须是纯的）。
   * 语义不受影响——本 effect 的依赖含 `orderSig`，草稿一改就会重跑；而它发出的
   * 请求最少也要等 300ms 去抖 + 一个往返，**任何回包都晚于本次 effect 的同步段**
   * ⇒ 不存在"ref 尚未更新"的窗口。 */
  const sigRef = useRef(orderSig);

  // 输入无效 → 渲染期同步清掉旧检查结果（adjust-state 模式）
  if (pv <= 0 || qv <= 0) {
    if (riskCheck !== null) setRiskCheck(null);
  }
  useEffect(() => {
    // 首行同步登记"当前订单"——它即最新发出的那张（见 sigRef 注释）
    sigRef.current = orderSig;
    if (pv <= 0 || qv <= 0) return;
    const sig = orderSig;
    const t = setTimeout(() => {
      setChecking(true);
      void checkOrderRisk({ symbol, side, price: pv, quantity: qv })
        .then((r) => {
          if (sigRef.current !== sig) return; // 过期回包：丢弃，绝不写 state
          setRiskCheck({ sig, result: r });
        })
        .catch(() => {
          // 失败即"无有效预检"（fail-closed）；同样只在仍是当前订单时才清，
          // 旧请求的失败不得把新签名的成功结论清掉。
          if (sigRef.current === sig) setRiskCheck(null);
        })
        .finally(() => setChecking(false));
    }, 300);
    return () => clearTimeout(t);
    // orderSig 是这轮请求的身份；effect 体内实际读取 symbol/side/pv/qv，
    // 一并列出以满足 exhaustive-deps（四者恰好构成 orderSig）。
  }, [orderSig, pv, qv, symbol, side]);

  /** 回到当前现价（显式动作，不会被 tick 自动触发）。 */
  function useCurrentPrice() {
    setPriceEdited(false);
    if (price != null) setP(fmt(price));
  }

  async function submit() {
    setSubmitting(true);
    setMsg(null);
    try {
      // 风控预检：未通过或结果与当前草稿不符（过期）则阻止下单
      if (!active || !active.allowed) {
        setMsg({
          ok: false,
          text: active
            ? (active.reasons[0] ?? "风控预检未通过")
            : "风控预检结果与当前订单不一致（订单已改动），请稍候重试",
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
    (active?.allowed === false);

  return (
    <div className="shrink-0 border-b border-zinc-100 px-3 py-2 dark:border-zinc-800/60">
      <div className="mb-2 flex gap-1">
        {(["buy", "sell"] as const).map((sd) => (
          <button
            key={sd}
            onClick={() => setSide(sd)}
            className={`flex-1 rounded py-1 text-sm font-medium ${
              side === sd ? (sd === "buy" ? "bg-up-deep text-white" : "bg-down-deep text-white") : "bg-zinc-100 text-zinc-600 dark:text-zinc-400 dark:bg-zinc-800"
            }`}
          >
            {sd === "buy" ? "买入" : "卖出"}
          </button>
        ))}
      </div>
      <div className="space-y-1.5 text-xs">
        <div className="flex items-center justify-between gap-2">
          <label className="flex flex-1 items-center gap-2">
            <span className="text-zinc-600 dark:text-zinc-400">价格</span>
            <input
              value={p}
              onFocus={() => setPriceEdited(true)}
              onChange={(e) => {
                setPriceEdited(true);
                setP(e.target.value);
              }}
              inputMode="decimal"
              className="ml-auto w-28 rounded border border-zinc-200 bg-transparent px-2 py-1 text-right font-mono text-zinc-900 outline-none focus:border-up/60 dark:border-zinc-700 dark:text-zinc-100"
            />
          </label>
          <button
            type="button"
            onClick={useCurrentPrice}
            disabled={price == null}
            title="把手填价换回当前现价（手填价默认锁定，不会被行情自动冲掉）"
            className="shrink-0 rounded border border-zinc-200 px-1.5 py-0.5 text-[10px] text-zinc-600 disabled:opacity-40 dark:border-zinc-700 dark:text-zinc-400"
          >
            使用现价
          </button>
        </div>
        {priceEdited && price != null && fmt(price) !== p && (
          <div className="flex justify-between text-[10px] text-amber-800 dark:text-amber-400">
            <span>已锁手填价（不再随行情刷新）</span>
            <span className="font-mono">现价 {fmt(price)}</span>
          </div>
        )}
        <label className="flex items-center justify-between gap-2">
          <span className="text-zinc-600 dark:text-zinc-400">数量</span>
          <input
            value={qty}
            onChange={(e) => setQty(e.target.value.replace(/[^0-9]/g, ""))}
            inputMode="numeric"
            className="w-28 rounded border border-zinc-200 bg-transparent px-2 py-1 text-right font-mono text-zinc-900 outline-none focus:border-up/60 dark:border-zinc-700 dark:text-zinc-100"
          />
        </label>
        <div className="flex justify-between text-zinc-600 dark:text-zinc-400">
          <span>预估金额</span>
          <span className="font-mono">{fmt(est)} + 费 {fmt(fee)}</span>
        </div>
        {active && (
          <div className="flex justify-between text-zinc-600 dark:text-zinc-400">
            <span>{side === "buy" ? "风控可买上限" : "可卖（T+1）"}</span>
            <span className="font-mono">{fmt(active.max_qty, 0)} 股</span>
          </div>
        )}
        {limitUp != null && side === "buy" && (
          <div className="flex justify-between text-zinc-600 dark:text-zinc-400">
            <span>涨停价</span>
            <span className="font-mono">{fmt(limitUp)}（≥则拒单）</span>
          </div>
        )}
        {limitDown != null && side === "sell" && (
          <div className="flex justify-between text-zinc-600 dark:text-zinc-400">
            <span>跌停价</span>
            <span className="font-mono">{fmt(limitDown)}（≤则拒单）</span>
          </div>
        )}
      </div>
      <button
        onClick={() => void submit()}
        disabled={submitting || bad}
        className={`mt-2 w-full rounded py-1.5 text-sm font-medium text-white disabled:opacity-40 ${side === "buy" ? "bg-up-deep" : "bg-down-deep"}`}
      >
        {submitting ? "提交中…" : `${side === "buy" ? "买入" : "卖出"} ${symbol}`}
      </button>
      {msg && <p className={`mt-1.5 text-xs ${msg.ok ? "text-emerald-700 dark:text-emerald-400" : "text-red-700 dark:text-red-400"}`}>{msg.text}</p>}
      {checking && !active && <p className="mt-1 text-[11px] text-zinc-600 dark:text-zinc-400">风控预检中…</p>}
      {active && active.warnings.length > 0 && (
        <p className="mt-1 text-[11px] text-amber-800 dark:text-amber-400">⚠ {active.warnings.join("；")}</p>
      )}
      {active?.allowed === false && (
        <p className="mt-1 text-[11px] text-red-700 dark:text-red-400">⛔ {active.reasons.join("；")}</p>
      )}
      {bad && pv > 0 && !submitting && (
        <p className="mt-1 text-[11px] text-zinc-600 dark:text-zinc-400">
          {side === "buy" && qv % 100 !== 0 ? "买入须为 100 股整数倍 " : ""}
          {limitUp != null && side === "buy" && pv >= limitUp ? "价格已达涨停，将被拒绝 " : ""}
          {limitDown != null && side === "sell" && pv <= limitDown ? "价格已达跌停，将被拒绝" : ""}
        </p>
      )}
    </div>
  );
}
