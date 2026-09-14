"""模拟账户历史污染范围的**只读审计**（`BUG-001` / 旧代号 E1，2026-09-14）。

## 背景

`R01`（挂单冻结重复扣款）与 `R02`（跨 await 陈旧账户 ⇒ 现金更新丢失）的**代码**
已于 `9e50775` 修复，但报告 §六「待完成的验收」首条要求另行清算：
**历史账户数据的污染范围、应恢复金额、以及受影响的核验结论**。
本脚本只做**审计**——不写任何数据、不重算任何余额。

## 判据：为什么用「现金守恒残差」而不是阈值筛查

⚠️ **本仓已踩过同类坑**（`retro-and-gaps.md` §6.2 P0-6）：用 `entry_price < 5`
这类**绝对阈值**当判据，会把「真·低价股 / ST」误判成污染——那是**筛查**条件，
不是判据。故本脚本的**判据**是同源守恒式，分子分母全部取自同一套函数：

```
残差 = (initial_cash − cash) − [ Σ(已成交买单实付) + Σ(挂单中买单冻结额) − Σ(卖出净收入) ]
```

- **为什么这个式子成立**：按修复前的代码，买入路径的资金动作只有三种——
  ① 直接成交：`cash -= 实付`；② 挂单：`cash -= 冻结额`，撤单 `cash += 冻结额`；
  ③ 挂单后成交：`cash -= 冻结额`，再 `cash -= 实付`（**冻结额从不归还**）。
  ⇒ 若全部正确，`initial_cash − cash` 恰等于「已成交买单实付 + 仍在挂单的冻结额 − 卖出净收入」。
- **残差 = 0** ⇒ 无污染；
- **残差 > 0** ⇒ 恰为 `R01` 未归还的冻结额，**残差本身就是应恢复金额**（口径：冻结额，
  非本金）；
- **残差 < 0** ⇒ 入账少于应扣，`R02`（并发丢失更新）的形态——两笔同时下单各自提交
  陈旧全量余额 ⇒ 只落一次扣款。

**同源保证**：`buy_freeze_amount` / `calc_fee` **直接从生产模块导入**，不在本脚本
重写一份（重写 = 制造第二个真相源，且会在费率改动后静默失真）。

## 为什么还要扫备份库

主库的 `paper_account.updated_at` 若晚于某些订单的创建时间，说明其间发生过 `reset`
（`reset()` 会清空订单与持仓并把 `cash` 打回 `initial_cash`）——**污染可能已被 reset
掩盖**。故必须把 `data/ashare.db.bak*` 快照一并只读扫过，否则会得出「现在干净 ⇒
从来干净」的错误结论。

用法：`cd backend && .venv/bin/python scripts/audit_paper_history.py`
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/（与其他 scripts 同约定）

from app.core.config import settings  # noqa: E402
from app.paper.engine import buy_freeze_amount, calc_fee  # noqa: E402

#: 残差容差（分）。SQLite 存的是 float，逐笔 round(…, 2) 后求和会有厘级浮点噪声。
TOL = 0.011


def _db_paths() -> list[tuple[str, Path]]:
    """主库 + 同目录全部备份快照。

    路径**调用时**从 `settings.database_url` 取（与生产同源），不在模块级固化——
    本仓已因「读侧自持路径常量」踩过读写分叉（`skyrocket` 档案权重恒 0，§6.6 行 16）。
    """
    url = settings.database_url
    if not url.startswith("sqlite:///"):
        raise SystemExit(f"本审计只支持 sqlite，当前 database_url={url!r}")
    main = Path(url.removeprefix("sqlite:///"))
    out = [("main", main)]
    if main.parent.is_dir():
        for p in sorted(main.parent.glob(main.name + ".bak*")):
            if p.is_file():
                out.append(("backup", p))
    return out


def _rows(con: sqlite3.Connection, sql: str) -> list[dict]:
    cur = con.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _audit_scope(orders: list[dict], acct: dict) -> dict:
    """单个 scope 的守恒对账。"""
    buys_filled = [o for o in orders if o["side"] == "buy" and o["status"] == "filled"]
    buys_pending = [o for o in orders if o["side"] == "buy" and o["status"] == "pending"]
    sells_filled = [o for o in orders if o["side"] == "sell" and o["status"] == "filled"]

    filled_cost = sum(
        round((o["filled_price"] or 0.0) * o["quantity"] + (o["fee"] or 0.0), 2) for o in buys_filled
    )
    pending_freeze = sum(buy_freeze_amount(o["price"], o["quantity"]) for o in buys_pending)
    sell_net = sum(
        round((o["filled_price"] or 0.0) * o["quantity"] - (o["fee"] or 0.0), 2) for o in sells_filled
    )

    deficit = acct["initial_cash"] - acct["cash"]
    expected = round(filled_cost + pending_freeze - sell_net, 2)
    residual = round(deficit - expected, 2)

    # 费率漂移自检（**保护上面的守恒判据本身**）：守恒式里的「实付」取**订单表记录值**
    # `fee`，若历史某轮的费率表与今天不同，残差会混入「费率差」而**不是**污染——
    # 那会把判据变成噪声源。故把记录值与当前 `calc_fee` 对一遍，逐笔列出不一致处，
    # 让「残差 ≠ 0」时能先排除这一解释。
    fee_drift = [
        {
            "id": o["id"],
            "side": o["side"],
            "recorded": round(o["fee"] or 0.0, 4),
            "recomputed": calc_fee(o["side"], o["filled_price"] or 0.0, o["quantity"]),
        }
        for o in orders
        if o["status"] == "filled"
        and o["filled_price"] is not None
        and abs((o["fee"] or 0.0) - calc_fee(o["side"], o["filled_price"], o["quantity"])) > 0.01
    ]

    # 筛查面（**不是判据**）：限价低于成交价的买单必然是「先挂单、后成交」⇒ 下单时被冻结过。
    # 只用于给残差定位到具体订单；真正的判据是上面的守恒残差。
    screened = [
        {
            "id": o["id"],
            "symbol": o["symbol"],
            "price": o["price"],
            "filled_price": o["filled_price"],
            "freeze": buy_freeze_amount(o["price"], o["quantity"]),
            "created_at": o["created_at"],
        }
        for o in buys_filled
        if o["filled_price"] is not None and o["price"] > o["filled_price"]
    ]

    if abs(residual) <= TOL:
        verdict = "clean"
    elif residual > 0:
        verdict = "R01_unreturned_freeze"
    else:
        verdict = "R02_lost_update_or_over_credit"

    return {
        "scope": acct["scope"],
        "cash": round(acct["cash"], 2),
        "initial_cash": round(acct["initial_cash"], 2),
        "deficit": round(deficit, 2),
        "filled_buy_cost": round(filled_cost, 2),
        "pending_buy_freeze": round(pending_freeze, 2),
        "sell_net": round(sell_net, 2),
        "expected_deficit": expected,
        "residual": residual,
        "verdict": verdict,
        "n_orders": len(orders),
        "n_filled_buy": len(buys_filled),
        "n_pending_buy": len(buys_pending),
        "screened_pending_then_filled": screened,
        "fee_drift": fee_drift,
        "account_updated_at": acct["updated_at"],
        "account_created_at": acct["created_at"],
    }


def _audit_snapshot(label: str, path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as exc:  # pragma: no cover - 环境问题
        print(f"  !! 无法只读打开 {path}: {exc}", file=sys.stderr)
        return None
    try:
        tabs = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
        if "paper_account" not in tabs:
            return None
        orders = _rows(
            con,
            "select id,scope,symbol,side,price,quantity,status,filled_price,fee,reason,"
            "created_at,updated_at from paper_order order by id",
        )
        accts = _rows(
            con, "select id,scope,cash,initial_cash,created_at,updated_at from paper_account order by id"
        )
        positions = _rows(
            con, "select scope,symbol,quantity,frozen_today,buy_date,cost_price from paper_position"
        )
    finally:
        con.close()

    return {
        "label": label,
        "path": str(path),
        "size": path.stat().st_size,
        "mtime": __import__("datetime").datetime.fromtimestamp(path.stat().st_mtime).isoformat(" ", "seconds"),
        "n_orders_total": len(orders),
        "n_positions_total": len(positions),
        "positions": positions,
        "scopes": [
            _audit_scope([o for o in orders if o["scope"] == a["scope"]], a) for a in accts
        ],
        "orders": orders,
    }


def main() -> int:
    print("=" * 78)
    print("模拟账户历史污染范围 · 只读审计（BUG-001 / 旧代号 E1）")
    print("判据 = 现金守恒残差（同源）｜残差>0 ⇒ R01 未归还冻结额；<0 ⇒ R02 丢失更新")
    print("=" * 78)

    total_residual = 0.0
    snapshots = []
    for label, path in _db_paths():
        snap = _audit_snapshot(f"{label}:{path.name}", path)
        if snap is not None:
            snapshots.append(snap)

    for snap in snapshots:
        print(f"\n### {snap['label']}")
        print(f"    path={snap['path']}  size={snap['size']}  mtime={snap['mtime']}")
        print(f"    订单 {snap['n_orders_total']} 行 ｜ 持仓 {snap['n_positions_total']} 行")
        for s in snap["scopes"]:
            print(
                f"    [{s['scope']:6s}] cash={s['cash']:.2f} initial={s['initial_cash']:.2f} "
                f"deficit={s['deficit']:.2f}"
            )
            print(
                f"             期望 deficit = 已成交买单 {s['filled_buy_cost']:.2f} "
                f"+ 挂单冻结 {s['pending_buy_freeze']:.2f} − 卖出净收入 {s['sell_net']:.2f} "
                f"= {s['expected_deficit']:.2f}"
            )
            print(
                f"             残差 = {s['residual']:.2f}  ⇒ 判定 {s['verdict']}"
                f"  （订单 {s['n_orders']} 笔：成交买 {s['n_filled_buy']} / 挂单买 {s['n_pending_buy']}）"
            )
            if s["fee_drift"]:
                print("             ⚠️ 费率漂移（记录 fee ≠ 当前 calc_fee）—— 残差须先扣除此项才能当污染:")
                for o in s["fee_drift"]:
                    print(f"                #{o['id']} {o['side']} 记录 {o['recorded']} vs 复算 {o['recomputed']}")
            if s["screened_pending_then_filled"]:
                print("             ⚠️ 筛查面（先挂单后成交，必然被冻结过）:")
                for o in s["screened_pending_then_filled"]:
                    print(f"                #{o['id']} {o['symbol']} 限价 {o['price']} "
                          f"成交 {o['filled_price']} 冻结额 {o['freeze']:.2f} @{o['created_at']}")
            total_residual += s["residual"]

    print("\n" + "=" * 78)
    print(f"全快照残差合计 = {total_residual:.2f} 元")
    if abs(total_residual) <= TOL:
        print("结论：污染范围 = 空集。无需恢复金额（应恢复 0.00 元）；无需重算。")
    else:
        print(f"结论：存在 {total_residual:+.2f} 元的未清算差额 ⇒ 需人工定性后再谈重算。")
    print("注：本脚本**只读**（`mode=ro`），未修改任何数据。")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
