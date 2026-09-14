"""模拟账本**只读对账器**（`IMP-003` / 报告 F1，2026-09-14）。

## 为什么需要它

R01（挂单冻结重复扣款）/ R02（跨 await 陈旧账户 ⇒ 丢失更新）/ R06（委托列表未带
`scope`）都是**代码已修、但「账本对不对」没人能一句话回答**的缺陷。报告 F1 因此把
「新增只读对账器」列为 R01/R02/R06 修复后的**首选**增量：把「资金扣得对不对、
预留与挂单是否成对、scope 有没有串、成交与持仓是否对得上」变成**可执行的四条不变量**，
而不是靠人翻流水。

## 四条不变量（每条都能追到具体订单）

| 检查 | 不变量 | 违反的含义 |
|---|---|---|
| `cash_conservation` | `initial_cash − cash == Σ(已成交买单实付) + Σ(挂单中买单冻结额) − Σ(卖出净收入)` | 残差 >0 = R01 未归还冻结额（**残差即应恢复金额**）；<0 = R02 丢失更新 |
| `reserved_vs_pending` | 冻结额只能由 **pending 买单**派生；卖挂单不得超可卖量；同键不得重复挂单 | 预留与未决单脱钩（撤单退不回 / 幂等破口） |
| `scope_isolation` | `paper_order` / `paper_position` 的 `scope` 必须是账户表的已登记域 | R06 形态：main 端点混入 shadow 记录 |
| `fills_vs_positions` | 逐 (scope, symbol)：`Σ买入成交量 − Σ卖出成交量 == 持仓数量` | 成交与持仓分家（成交了没持仓 / 持仓无成交来源） |

## 判据纪律：**不用阈值筛查**

⚠️ 本仓已踩过（`retro-and-gaps.md` §6.2 P0-6）：用 `entry_price < 5` 这类**绝对阈值**
当判据，会把「真·低价股 / ST」误判成污染——那是**筛查**条件，不是判据。
故此处一律用**同源恒等式**：`buy_freeze_amount` / `calc_fee` 直接**从生产模块导入**，
不在本模块重写一份（重写 = 第二个真相源，费率一改就静默失真）。

## 只读保证

本模块**只 SELECT**：不改余额、不重算、不落库。异常只以 `findings` 返回，
由调用方决定怎么呈现。**重算是资金口径变更，须用户拍板**（见 `BUG-001` 的结论：
本次实测污染为空集，故无需重算）。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.paper import SCOPE_MAIN, SCOPE_SHADOW, PaperAccount, PaperOrder, PaperPosition
from app.paper.engine import buy_freeze_amount, calc_fee

#: 判据容差（分）：SQLite 存 float，逐笔 round(…, 2) 求和后有厘级浮点噪声。
TOL = 0.011

#: 账户域闭集（新增域必须同时改这里与 `models/paper.py` 的常量）。
KNOWN_SCOPES = (SCOPE_MAIN, SCOPE_SHADOW)


def _finding(check: str, severity: str, message: str, *, symbol: str | None = None,
             order_ids: list[int] | None = None) -> dict:
    return {
        "check": check,
        "severity": severity,  # error（账本不自洽）/ warning（可疑但可能合法）
        "message": message,
        "symbol": symbol,
        "order_ids": order_ids or [],
    }


def _cash_conservation(account: PaperAccount | None, orders: list[PaperOrder]) -> tuple[list[dict], dict]:
    """不变量一：资金守恒。返回 (findings, metrics)。"""
    out: list[dict] = []
    # 费率漂移自检（**保护上面这条判据本身**）：守恒式里的「实付」取订单表**记录值** `fee`。
    # 若历史某轮的费率表与今天不同，残差会混入「费率差」而**不是**污染——那会把判据变成
    # 噪声源。故先把记录值与当前 `calc_fee` 对一遍，逐笔列出不一致处，
    # 让「残差 ≠ 0」时能先排除这一解释。
    for o in orders:
        if o.status != "filled" or o.filled_price is None:
            continue
        recomputed = calc_fee(o.side, o.filled_price, o.quantity)
        if abs((o.fee or 0.0) - recomputed) > 0.01:
            out.append(_finding(
                "cash_conservation", "warning",
                f"#{o.id} 记录费用 {o.fee:.4f} ≠ 按当前费率复算 {recomputed:.4f}"
                f"（费率口径漂移；此时残差须先扣掉这项才能当污染）",
                symbol=o.symbol, order_ids=[o.id]))
    if account is None:
        return out + [_finding("cash_conservation", "error", "账户行缺失，无法对账")], {}
    filled_buy_cost = sum(
        round((o.filled_price or 0.0) * o.quantity + (o.fee or 0.0), 2)
        for o in orders if o.side == "buy" and o.status == "filled"
    )
    pending_freeze = sum(
        buy_freeze_amount(o.price, o.quantity)
        for o in orders if o.side == "buy" and o.status == "pending"
    )
    sell_net = sum(
        round((o.filled_price or 0.0) * o.quantity - (o.fee or 0.0), 2)
        for o in orders if o.side == "sell" and o.status == "filled"
    )
    deficit = account.initial_cash - account.cash
    expected = round(filled_buy_cost + pending_freeze - sell_net, 2)
    residual = round(deficit - expected, 2)

    if abs(residual) > TOL:
        if residual > 0:
            kind = ("R01 形态：挂单成交未归还冻结额（冻结被扣两次）")
        else:
            kind = "R02 形态：入账少于应扣（并发丢失更新 / 重复入账）"
        # 可追订单：限价低于成交价的买单必然「先挂单、后成交」⇒ 下单时冻结过。
        suspects = [
            o for o in orders
            if o.side == "buy" and o.status == "filled"
            and o.filled_price is not None and o.price > o.filled_price
        ]
        out.append(_finding(
            "cash_conservation", "error",
            f"资金守恒残差 {residual:+.2f} 元（{kind}）；"
            f"明细 = 期初 {account.initial_cash:.2f} − 现余 {account.cash:.2f} = {deficit:.2f}，"
            f"应为 已成交买单 {filled_buy_cost:.2f} + 挂单冻结 {pending_freeze:.2f} "
            f"− 卖出净收入 {sell_net:.2f} = {expected:.2f}",
            order_ids=[o.id for o in suspects],
        ))
    return out, {
        "cash": round(account.cash, 2),
        "initial_cash": round(account.initial_cash, 2),
        "deficit": round(deficit, 2),
        "expected_deficit": expected,
        "residual": residual,
    }


def _reserved_vs_pending(db: Session, scope: str, orders: list[PaperOrder],
                         positions: list[PaperPosition]) -> list[dict]:
    """不变量二：预留与未决单一致（冻结额只能由 pending 买单派生）。"""
    out: list[dict] = []
    pos_by_symbol = {p.symbol: p for p in positions}
    seen: dict[tuple[str, str, float], list[int]] = {}
    for o in orders:
        if o.status != "pending":
            continue
        if o.side == "buy":
            if buy_freeze_amount(o.price, o.quantity) <= 0:
                out.append(_finding(
                    "reserved_vs_pending", "error",
                    f"挂单买单 #{o.id} 冻结额 ≤ 0（价 {o.price} 量 {o.quantity}）",
                    symbol=o.symbol, order_ids=[o.id]))
            seen.setdefault((o.symbol, o.side, round(o.price, 4)), []).append(o.id)
        else:
            pos = pos_by_symbol.get(o.symbol)
            available = pos.available if pos is not None else 0
            if o.quantity > available:
                out.append(_finding(
                    "reserved_vs_pending", "error",
                    f"卖挂单 #{o.id} 数量 {o.quantity} 超可卖 {available}"
                    f"（持仓 {pos.quantity if pos else 0} / 当日冻结 "
                    f"{pos.frozen_today if pos else 0}）——该单不可能成交",
                    symbol=o.symbol, order_ids=[o.id]))
    # 幂等破口：同键多条 pending 意味着「同一意向挂了两次」（影子执行器要求同日幂等）
    for (symbol, side, price), ids in seen.items():
        if len(ids) > 1:
            out.append(_finding(
                "reserved_vs_pending", "warning",
                f"{symbol} {side} 价 {price} 存在 {len(ids)} 条重复挂单（幂等破口）",
                symbol=symbol, order_ids=ids))
    return out


def _scope_isolation(accounts: list[PaperAccount], orders: list[PaperOrder],
                     positions: list[PaperPosition]) -> list[dict]:
    """不变量三：scope 隔离（值域闭集 + 无孤儿行）。"""
    out: list[dict] = []
    known = {a.scope for a in accounts}
    for o in orders:
        if o.scope not in KNOWN_SCOPES:
            out.append(_finding("scope_isolation", "error",
                                f"委托 #{o.id} 的 scope={o.scope!r} 不在已知账户域内",
                                symbol=o.symbol, order_ids=[o.id]))
        elif o.scope not in known:
            out.append(_finding("scope_isolation", "error",
                                f"委托 #{o.id} 的 scope={o.scope!r} 无对应账户行",
                                symbol=o.symbol, order_ids=[o.id]))
    for p in positions:
        if p.scope not in KNOWN_SCOPES:
            out.append(_finding("scope_isolation", "error",
                                f"持仓 {p.symbol} 的 scope={p.scope!r} 不在已知账户域内",
                                symbol=p.symbol))
        elif p.scope not in known:
            out.append(_finding("scope_isolation", "error",
                                f"持仓 {p.symbol} 的 scope={p.scope!r} 无对应账户行",
                                symbol=p.symbol))
    return out


def _fills_vs_positions(orders: list[PaperOrder], positions: list[PaperPosition]) -> list[dict]:
    """不变量四：成交与持仓变动对照（净成交量 == 持仓量）。"""
    out: list[dict] = []
    net: dict[str, int] = {}
    ids: dict[str, list[int]] = {}
    for o in orders:
        if o.status != "filled":
            continue
        sign = 1 if o.side == "buy" else -1
        net[o.symbol] = net.get(o.symbol, 0) + sign * o.quantity
        ids.setdefault(o.symbol, []).append(o.id)
    for p in positions:
        if p.quantity <= 0:
            out.append(_finding("fills_vs_positions", "warning",
                                f"{p.symbol} 存在 0 股持仓行（应为垃圾行）", symbol=p.symbol))
        expected = net.pop(p.symbol, 0)
        if expected != p.quantity:
            out.append(_finding(
                "fills_vs_positions", "error",
                f"{p.symbol} 成交净量 {expected} ≠ 持仓 {p.quantity}"
                f"（差额 {p.quantity - expected:+d}）",
                symbol=p.symbol, order_ids=ids.get(p.symbol, [])))
    for symbol, qty in net.items():  # 有成交却无持仓
        if qty != 0:
            out.append(_finding(
                "fills_vs_positions", "error",
                f"{symbol} 有成交净量 {qty} 但无持仓行",
                symbol=symbol, order_ids=ids.get(symbol, [])))
    return out


def reconcile_scope(db: Session, scope: str) -> dict:
    """对账单个 scope（**只读**）。"""
    account = db.query(PaperAccount).filter(PaperAccount.scope == scope).one_or_none()
    orders = db.query(PaperOrder).filter(PaperOrder.scope == scope).all()
    positions = db.query(PaperPosition).filter(PaperPosition.scope == scope).all()

    cash_findings, metrics = _cash_conservation(account, orders)
    findings = (
        cash_findings
        + _reserved_vs_pending(db, scope, orders, positions)
        + _scope_isolation([account] if account else [], orders, positions)
        + _fills_vs_positions(orders, positions)
    )
    return {
        "scope": scope,
        "ok": not any(f["severity"] == "error" for f in findings),
        "counts": {"orders": len(orders), "positions": len(positions)},
        "metrics": metrics,
        "findings": findings,
    }


def reconcile(session_factory) -> dict:
    """对账全部已知 scope。复用引擎的 session 工厂（与生产同一条连接路径）。"""
    with session_factory() as db:
        scopes = [r.scope for r in db.query(PaperAccount).all()]
        reports = [reconcile_scope(db, s) for s in sorted(set(scopes))]
    return {
        "ok": all(r["ok"] for r in reports),
        "scopes": reports,
    }
