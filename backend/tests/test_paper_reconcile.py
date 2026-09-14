"""模拟账本只读对账器自证测试（`IMP-003` / 报告 F1，2026-09-14）。

## 为什么每条不变量都要有「注入即精确判红」的用例

对账器的失效方式与它要抓的缺陷**同形**：`ok=true` 既可能真干净、也可能判据压根没接上
（[[KB-ENG-72]]：**清单覆盖了什么 ≠ 真实覆盖面**）。故每条不变量都配一个**定向注入**，
并断言「报的是哪一条 check、且 `order_ids` 指到了那一单」——
只断言 `ok is False` 的话，把四条判据全换成 `return []` 之外的任意错法都可能仍绿。

## 构造方式

直接写 ORM 行（不跑撮合）——对账器是**纯读**，把账本状态直接摆成想验证的形态，
比"驱动撮合摆出该形态"更短也更不会因撮合逻辑变动而漂移。同源公式
（`buy_freeze_amount` / `calc_fee`）从生产模块取，不在本文件重算。
"""
from __future__ import annotations

from app.core.db import get_engine, get_session_factory
from app.models.paper import SCOPE_MAIN, SCOPE_SHADOW, PaperAccount, PaperOrder, PaperPosition
from app.models.watchlist import Base
from app.paper.engine import calc_fee
from app.paper.reconcile import reconcile, reconcile_scope

SF = get_session_factory()


def _wipe() -> None:
    Base.metadata.create_all(get_engine())
    with SF() as db:
        for model in (PaperAccount, PaperPosition, PaperOrder):
            db.query(model).delete()
        db.commit()


def _account(scope: str, cash: float = 1_000_000.0, initial: float = 1_000_000.0) -> None:
    with SF() as db:
        db.add(PaperAccount(scope=scope, cash=cash, initial_cash=initial))
        db.commit()


def _order(scope: str, *, side: str = "buy", status: str = "pending", price: float = 10.0,
           quantity: int = 100, filled_price: float | None = None, fee: float = 0.0,
           symbol: str = "600000") -> int:
    with SF() as db:
        o = PaperOrder(scope=scope, symbol=symbol, side=side, price=price, quantity=quantity,
                       status=status, filled_price=filled_price, fee=fee)
        db.add(o)
        db.commit()
        return o.id


def _position(scope: str, *, symbol: str = "600000", quantity: int = 100,
              frozen_today: int = 0, cost_price: float = 10.0) -> None:
    with SF() as db:
        db.add(PaperPosition(scope=scope, symbol=symbol, quantity=quantity,
                             frozen_today=frozen_today, cost_price=cost_price))
        db.commit()


def _checks(report: dict) -> set[str]:
    return {f["check"] for f in report["findings"]}


def _reconcile(scope: str) -> dict:
    """单 scope 对账（省掉每处 with/commit 样板）。"""
    with SF() as db:
        return reconcile_scope(db, scope)


def test_clean_empty_ledger_is_ok():
    _wipe()
    _account(SCOPE_MAIN)
    _account(SCOPE_SHADOW)
    out = reconcile(SF)
    assert out["ok"] is True
    assert [s["scope"] for s in out["scopes"]] == [SCOPE_MAIN, SCOPE_SHADOW]
    assert all(s["findings"] == [] for s in out["scopes"])


def test_clean_pending_freeze_is_ok():
    """挂单冻结「原样对得上」⇒ 干净（这是历史账本的实际形态，勿误报）。"""
    _wipe()
    price, qty = 1.82, 100
    freeze = price * qty + calc_fee("buy", price, qty)  # 187.00
    _account(SCOPE_MAIN, cash=1_000_000.0 - 2 * freeze)
    _order(SCOPE_MAIN, price=price, quantity=qty)
    _order(SCOPE_MAIN, price=price, quantity=qty)
    out = _reconcile(SCOPE_MAIN)
    # ⚠️ 重复挂单会出 warning（见 test_duplicate_pending_orders_warn），故此处不看 ok，
    # 只看**资金守恒这一条**成立——判据各管各的，不互相污染。
    assert _checks(out) == {"reserved_vs_pending"}
    assert out["metrics"]["residual"] == 0.0
    assert out["metrics"]["deficit"] == round(2 * freeze, 2)


def test_injected_r01_unreturned_freeze_is_caught_with_order_id():
    """R01 形态：挂单成交后冻结额没还 ⇒ 残差 == 冻结额，且指向该单。"""
    _wipe()
    price, qty, fill = 10.0, 100, 9.9
    freeze = price * qty + calc_fee("buy", price, qty)
    real_cost = fill * qty + calc_fee("buy", fill, qty)
    # 正确：只扣实付。注入：再少 freeze（= 冻结额被扣了两次）
    _account(SCOPE_MAIN, cash=1_000_000.0 - real_cost - freeze)
    oid = _order(SCOPE_MAIN, status="filled", price=price, quantity=qty,
                 filled_price=fill, fee=calc_fee("buy", fill, qty))
    _position(SCOPE_MAIN, quantity=qty, cost_price=fill)  # 让其余三条不变量保持干净
    out = _reconcile(SCOPE_MAIN)
    assert out["ok"] is False
    assert _checks(out) == {"cash_conservation"}, out["findings"]
    f = out["findings"][0]
    assert f["severity"] == "error"
    assert abs(out["metrics"]["residual"] - round(freeze, 2)) < 0.011
    assert oid in f["order_ids"]


def test_injected_r02_lost_update_is_caught():
    """R02 形态：入账少于应扣（并发丢失更新）⇒ 残差为负。"""
    _wipe()
    fill, qty = 10.0, 100
    real_cost = fill * qty + calc_fee("buy", fill, qty)
    # 注入：只扣了一次的 60%（丢更新）
    _account(SCOPE_MAIN, cash=1_000_000.0 - real_cost * 0.6)
    _order(SCOPE_MAIN, status="filled", price=fill, quantity=qty,
           filled_price=fill, fee=calc_fee("buy", fill, qty))
    _position(SCOPE_MAIN, quantity=qty, cost_price=fill)
    out = _reconcile(SCOPE_MAIN)
    assert out["ok"] is False
    assert _checks(out) == {"cash_conservation"}
    assert out["metrics"]["residual"] < 0
    assert "R02 形态" in out["findings"][0]["message"]


def test_sell_pending_beyond_available_is_caught():
    """卖挂单数量超可卖 ⇒ 判红并指向该单（不可能成交）。"""
    _wipe()
    fill, qty = 10.0, 100
    _account(SCOPE_MAIN, cash=1_000_000.0 - (fill * qty + calc_fee("buy", fill, qty)))
    # 先造一笔合法成交+持仓（避免混入 fills_vs_positions 的噪声），可卖 = 100 − 100 = 0
    _order(SCOPE_MAIN, status="filled", price=fill, quantity=qty,
           filled_price=fill, fee=calc_fee("buy", fill, qty))
    _position(SCOPE_MAIN, quantity=qty, frozen_today=qty)
    oid = _order(SCOPE_MAIN, side="sell", status="pending", quantity=qty)
    out = _reconcile(SCOPE_MAIN)
    assert out["ok"] is False
    assert _checks(out) == {"reserved_vs_pending"}, out["findings"]
    assert oid in out["findings"][0]["order_ids"]


def test_duplicate_pending_orders_warn():
    """同键多条 pending = 幂等破口（warning；旧账本真有此形态，不是 error）。"""
    _wipe()
    price, qty = 1.82, 100
    freeze = price * qty + calc_fee("buy", price, qty)
    _account(SCOPE_MAIN, cash=1_000_000.0 - 2 * freeze)
    a = _order(SCOPE_MAIN, price=price, quantity=qty)
    b = _order(SCOPE_MAIN, price=price, quantity=qty)
    out = _reconcile(SCOPE_MAIN)
    # 资金守恒仍成立 ⇒ 这就是「实测到的干净形态」，只该出 warning
    assert out["metrics"]["residual"] == 0.0
    assert _checks(out) == {"reserved_vs_pending"}
    f = out["findings"][0]
    assert f["severity"] == "warning"
    assert {a, b} <= set(f["order_ids"])


def test_unknown_scope_is_caught():
    """scope 越界（R06 形态：记录混入未知域）⇒ 判红并指向该单。"""
    _wipe()
    _account(SCOPE_MAIN)
    oid = _order("rogue", side="buy")
    with SF() as db:
        rogue = reconcile_scope(db, "rogue")
    assert rogue["ok"] is False
    iso = [f for f in rogue["findings"] if f["check"] == "scope_isolation"]
    assert iso, rogue["findings"]
    assert oid in iso[0]["order_ids"]


def test_order_scope_without_account_row_is_caught():
    """已知域但无账户行：委托指向一个不存在的账户 ⇒ 判红。"""
    _wipe()
    _account(SCOPE_MAIN)
    with SF() as db:
        # 只有 shadow 的委托、却没有任何 shadow 账户行
        db.add(PaperOrder(scope=SCOPE_SHADOW, symbol="600000", side="buy",
                          price=10.0, quantity=100, status="pending"))
        db.commit()
        out = reconcile_scope(db, SCOPE_SHADOW)
    assert out["ok"] is False
    msgs = " ".join(f["message"] for f in out["findings"])
    assert "无对应账户行" in msgs


def test_fills_vs_positions_mismatch_is_caught():
    """净成交量 ≠ 持仓量 ⇒ 判红并附订单号。"""
    _wipe()
    fill, qty = 10.0, 100
    _account(SCOPE_MAIN, cash=1_000_000.0 - (fill * qty + calc_fee("buy", fill, qty)))
    oid = _order(SCOPE_MAIN, status="filled", price=fill, quantity=qty,
                 filled_price=fill, fee=calc_fee("buy", fill, qty))
    _position(SCOPE_MAIN, quantity=200, cost_price=fill)  # 多出 100
    out = _reconcile(SCOPE_MAIN)
    assert out["ok"] is False
    assert _checks(out) == {"fills_vs_positions"}, out["findings"]
    assert oid in out["findings"][0]["order_ids"]
    assert "≠ 持仓" in out["findings"][0]["message"]


def test_fills_without_position_row_is_caught():
    _wipe()
    fill, qty = 10.0, 100
    _account(SCOPE_MAIN, cash=1_000_000.0 - (fill * qty + calc_fee("buy", fill, qty)))
    _order(SCOPE_MAIN, status="filled", price=fill, quantity=qty,
           filled_price=fill, fee=calc_fee("buy", fill, qty))
    out = _reconcile(SCOPE_MAIN)
    assert out["ok"] is False
    assert _checks(out) == {"fills_vs_positions"}, out["findings"]
    assert "无持仓行" in out["findings"][0]["message"]


def test_zero_quantity_position_warns_only():
    """0 股持仓行是垃圾行 ⇒ warning，不该判 error（否则每次对账都假红）。"""
    _wipe()
    _account(SCOPE_MAIN)
    _position(SCOPE_MAIN, quantity=0)
    out = _reconcile(SCOPE_MAIN)
    assert out["ok"] is True
    assert [f["severity"] for f in out["findings"]] == ["warning"]


def test_missing_account_is_caught():
    _wipe()
    with SF() as db:
        out = reconcile_scope(db, SCOPE_MAIN)
    assert out["ok"] is False
    assert "账户行缺失" in out["findings"][0]["message"]


def test_fee_drift_is_warned_not_silently_baked_into_residual():
    """记录费用 ≠ 当前费率复算 ⇒ 出 warning（保护守恒判据不被费率差污染）。

    为什么这条必要：守恒式用的是订单表**记录值** `fee`。若历史某轮费率不同，
    残差里会混进「费率差」，让「残差 ≠ 0」有两种解释而分不开。故必须显式报出来，
    而不是让使用者把费率漂移误当资金污染。
    """
    _wipe()
    fill, qty = 10.0, 100
    correct_fee = calc_fee("buy", fill, qty)
    _account(SCOPE_MAIN, cash=1_000_000.0 - (fill * qty + correct_fee))
    oid = _order(SCOPE_MAIN, status="filled", price=fill, quantity=qty,
                 filled_price=fill, fee=99.0)  # 故意与费率不符
    _position(SCOPE_MAIN, quantity=qty, cost_price=fill)
    out = _reconcile(SCOPE_MAIN)
    assert _checks(out) == {"cash_conservation"}
    drift = [f for f in out["findings"] if "费率口径漂移" in f["message"]]
    assert drift and drift[0]["severity"] == "warning"
    assert oid in drift[0]["order_ids"]


def test_reconcile_is_read_only():
    """对账前后账本逐字段不变（本模块只 SELECT）。"""
    _wipe()
    _account(SCOPE_MAIN, cash=999_626.0)
    _order(SCOPE_MAIN, price=1.82, quantity=100)
    _order(SCOPE_MAIN, price=1.82, quantity=100)
    _position(SCOPE_MAIN, quantity=0)

    def snap() -> dict:
        with SF() as db:
            return {
                "acc": [(a.scope, a.cash, a.initial_cash) for a in db.query(PaperAccount).all()],
                "ord": [(o.id, o.status, o.filled_price, o.fee) for o in db.query(PaperOrder).all()],
                "pos": [(p.symbol, p.quantity, p.frozen_today) for p in db.query(PaperPosition).all()],
            }

    before = snap()
    reconcile(SF)
    assert snap() == before
