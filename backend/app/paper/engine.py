"""模拟交易撮合引擎（Phase 6 slice 1）。

A股规则落地（full.md §14/§2.4，配置化）：
- T+1：当日买入 frozen，**买入日之后的首个交易日**解冻（官方交易日历，日历不可用显式降级为自然日）
- 涨停无法买入 / 跌停无法卖出（涨跌停价来自行情源）
- 停牌（无价格）拒绝；买入数量须为 100 股整数倍；资金/可卖数量校验
- 限价撮合：买价 ≥ 现价 按现价成交，否则挂单轮询；卖反向
- 费用：佣金(万2.5, 最低5元) + 卖出印花税(0.05%) + 过户费(0.001%, 双边)；滑点暂为 0（配置化保留）

⚠️ 解冻是「结算」而非「读取的副产品」（R04，2026-09-14）：
`available = quantity - frozen_today` 是**派生值**，冻结字段不清零它就是错的。
故解冻入口有两个且都必须调 `_unfreeze`——写入路径（`place_order`）与
只读路径（`settle_t1`：离场监护、持仓快照）。只修写入路径等于没修。
"""
from __future__ import annotations

import logging
import math
from datetime import date, datetime

from sqlalchemy.orm import Session

from app.core.bjtime import beijing_today
from app.core.db import utcnow
from app.models.paper import PaperAccount, PaperOrder, PaperPosition

log = logging.getLogger(__name__)


def _as_ymd(value) -> str:
    """把交易日/买入日归一成 `YYYYMMDD` 字符串；不可识别返回空串。

    为什么必须归一（R04，2026-09-14）：`trading_days_fn` 有**两种真实来源且类型不同**——
    ① `main.hub_trading_days()` → ths `get_trading_days()`，返回 **YYYYMMDD 字符串**；
    ② `trade_calendar.trading_days()` → **`datetime.date`**（落盘 `trade_calendar.json` 同）。
    旧实现直接 `d > today`（today 是字符串）⇒ 传 `date` 时抛 `TypeError`，
    被 `except Exception` 吞成"日历不可用"，**静默退回自然日口径**：
    两种来源里有一种是坏的，症状却是"看起来正常"。
    """
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y%m%d")
    s = str(value).strip()
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":  # ISO：2026-09-11 → 20260911
        s = s[:4] + s[5:7] + s[8:10]
    return s if (len(s) == 8 and s.isdigit()) else ""


#: T+1 降级告警去重（(日期, scope)）：解冻在每个下单请求上都会被求值，不节流会刷屏。
_T1_FALLBACK_LOGGED: set[str] = set()

FEE = {
    "commission_rate": 0.00025,  # 佣金万2.5
    "commission_min": 5.0,
    "stamp_tax": 0.0005,  # 印花税（卖出）
    "transfer_fee": 0.00001,  # 过户费万0.1（双边）
    "slippage": 0.0,
}


def calc_fee(side: str, price: float, qty: int) -> float:
    commission = max(price * qty * FEE["commission_rate"], FEE["commission_min"])
    fee = commission + price * qty * FEE["transfer_fee"]  # 过户费双边
    if side == "sell":
        fee += price * qty * FEE["stamp_tax"]
    return round(fee + price * qty * FEE["slippage"], 2)


def _today() -> str:
    # 交易日归属固定北京日历——utcnow().astimezone() 按进程时区取日期，
    # 在 UTC 环境（CI）的北京时间 00:00-08:00 会归属到昨天（行为在 +8 机器不变）
    return beijing_today().strftime("%Y%m%d")


def is_valid_price(v) -> bool:
    """价格有效性（**有限且 > 0**）的**唯一判据**。

    为什么必须单点（2026-09-14，R05）：成交路径有三条入口——`place_order`
    下单校验、`match_pending` 挂单撮合、`_fill`/`_fill_sell` 成交落账，
    历史上只有第一条做了 `<= 0` 检查，后两条各写各的（或什么都没写）。

    两个反直觉的坑，单靠 `<= 0` 都挡不住：
    - **NaN**：`nan <= 0` 为 **False**（NaN 与任何值比较都是 False）
      ⇒ `if price <= 0` 放行 NaN；上游 `Quote` 的 `_clean` 虽已清洗 NaN，
        但 `match_pending` 直接消费 `quote.price`，且真实行情源并非都过 `Quote`。
    - **0**：`match_pending` 原只判 `quote.price is None`，价格 0 会**按 0 元成交**
      ⇒ 白送持仓、现金只加费用差额，属"数字出得来、账目是错的"。
    - **inf**：`inf > 0` 成立，会一路算进 `cost` 变成 `inf`，污染余额。
    """
    return (
        isinstance(v, (int, float))
        and not isinstance(v, bool)  # bool 是 int 子类，True 会被当成价 1
        and math.isfinite(v)
        and v > 0
    )


def limit_block_reason(quote, side: str, price: float | None) -> str | None:
    """涨跌停硬拦截（红线 5）的**唯一判据**。返回 `None` = 放行，否则返回中文原因。

    为什么单独抽成函数（2026-09-11 S1-4）：`place_order` 与 `match_pending`
    是两条独立的成交路径，历史上只有前者做了拦截，后者直接按现价成交
    ⇒ 挂单在股价冲上涨停后仍会成交，等于在涨停板买入。

    **缺限价一律不视为"无涨跌停"**。原写法 `if quote.limit_up_price and ...`
    在限价为 `None`（或 0）时整体短路，守卫静默失效且与"该票今天没触板"
    完全不可区分。限价来自链上腾讯单源补价、曾真实被封，所以缺失是
    **会发生的状态**而非异常路径；此处取保守口径（判不了就不放行），
    由调用方把它变成**可见**的拒单原因或跳过日志。
    """
    if side == "buy":
        up = quote.limit_up_price if (quote.limit_up_price or 0) > 0 else None
        if up is None:
            return "缺涨停价，无法校验涨停拦截，已拒绝买入（行情源未提供限价）"
        if price is not None and price >= up:
            return f"涨停价 {up}，无法买入"
    else:
        down = quote.limit_down_price if (quote.limit_down_price or 0) > 0 else None
        if down is None:
            return "缺跌停价，无法校验跌停拦截，已拒绝卖出（行情源未提供限价）"
        if price is not None and price <= down:
            return f"跌停价 {down}，无法卖出"
    return None


def buy_freeze_amount(price: float, qty: int) -> float:
    """买入**挂单**的冻结额（含预扣佣金）——与「实付」是两个不同的量。

    买入路径上资金发生两次动作：下单冻结（限价低于现价 ⇒ 挂单等成交）、
    撮合实付。二者必须**成对**：`cancel` 释放冻结、`match_pending` 在成交前
    释放冻结再由 `_fill` 扣实付。

    历史缺陷（2026-09-14 修复）：`match_pending` 直接调 `_fill` 而**没有**释放
    冻结 ⇒ 同一笔钱被扣两次（冻结额永不归还），账户资金系统性偏低，
    而模拟盘正是「胜率基线」的来源。唯一覆盖该路径的用例只断言
    `status == "filled"`，**未断言 cash**，所以门禁全绿也照不出来。
    """
    return price * qty + calc_fee("buy", price, qty)


class PaperTradingEngine:
    def __init__(
        self,
        session_factory,
        quote_fn,
        trading_days_fn=None,
        *,
        scope: str = "main",
        risk_engine=None,
    ):
        self._sf = session_factory
        self._quote_fn = quote_fn  # async (symbol) -> Quote | None（走实时链）
        self._tdays_fn = trading_days_fn  # async () -> list[str] | None
        #: 账户域：main=交易页签；shadow=每日精选影子持仓（数据隔离，互不可见）
        self.scope = scope
        #: 风控硬拦截（retro §6.5b #2，2026-09-13 拍板：check_order 从「仅 UI 预检」
        #: 升级为撮合层拦截）。边界刻意收窄为 **仅 main 账户 + 仅买入方向**：
        #: - **shadow 豁免**：影子账户是研究仪器，测的是每日精选策略本身，
        #:   风控否决会污染 A/B 口径（其上游闸门 gate.py 已各自把关）；
        #: - **卖出永不拦截**：卖出是减风险动作，风控的目的是阻止加风险——
        #:   拦自损卖出（exit_engine 硬止损走本引擎卖出路径）只会放大风险；
        #: - position_engine 的买点自动执行同走 main 买入，一并通过本闸
        #:   （其拒绝路径已优雅呈现「撮合拒绝：<原因>」）。
        #: 挂单轮询（match_pending）**不**重复风控：资金已在下单时冻结，
        #: 撮合期再否决会留下既不可成交也难自解释的冻结挂单——风控时点是下单。
        self._risk_engine = risk_engine

    # ---------- 账户 ----------

    def _account(self, db: Session) -> PaperAccount:
        """在**给定 session 内**取本账户（缺失则建）——资金读写的唯一入口。

        与 `ensure_account()` 的区别：后者自开 session 并在 return 前关闭，调用方
        拿到的是一个**已脱离 session** 的对象；把它 `db.add()` 回另一个 session 会
        触发**整行回写**，等于用窗口前的旧余额覆盖并发写入（下单路径上还有
        `_unfreeze` / 风控预检等多次 `await`，窗口从毫秒到秒级）。

        资金是模拟盘唯一的权威状态，故凡是「读余额 → 改余额 → 提交」必须在
        同一个 session 内完成。
        """
        acc = db.query(PaperAccount).filter(PaperAccount.scope == self.scope).first()
        if acc is None:
            acc = PaperAccount(scope=self.scope, cash=1_000_000.0, initial_cash=1_000_000.0)
            db.add(acc)
            db.flush()
        return acc

    def ensure_account(self) -> PaperAccount:
        """跨 session 的只读快照入口（端点/影子账户用）。**不要**用它参与读改写。"""
        with self._sf() as db:
            acc = self._account(db)
            db.commit()
            db.refresh(acc)
            return acc

    # ---------- 重置 ----------

    def reset(self, initial_cash: float | None = None, *, source: str = "engine") -> PaperAccount:
        """清空全部持仓与委托，账户资金回到初始额度。

        硬约束：仅重置模拟账户，不触碰任何外部接口；重置后账单历史一并清空，
        因为成交记录挂靠在订单表上，保留订单会导致持仓与历史不一致。

        审计：重置是破坏性操作且曾出现过"账户莫名被清空、无法定位来源"的情况，
        因此必须单行记录 **重置前状态**（持仓数/委托数/资金）+ 触发来源，
        而不是只记结果——事后定位全靠这条。
        """
        with self._sf() as db:
            pos_before = db.query(PaperPosition).filter(PaperPosition.scope == self.scope).count()
            ord_before = db.query(PaperOrder).filter(PaperOrder.scope == self.scope).count()
            old = db.query(PaperAccount).filter(PaperAccount.scope == self.scope).first()
            cash_before = old.cash if old else None
            initial_before = old.initial_cash if old else None

            db.query(PaperPosition).filter(PaperPosition.scope == self.scope).delete()
            db.query(PaperOrder).filter(PaperOrder.scope == self.scope).delete()
            acc = db.query(PaperAccount).filter(PaperAccount.scope == self.scope).first()
            if acc is None:
                acc = PaperAccount(scope=self.scope, cash=1_000_000.0, initial_cash=1_000_000.0)
            if initial_cash is not None and initial_cash > 0:
                acc.initial_cash = float(initial_cash)
            acc.cash = acc.initial_cash
            acc.updated_at = utcnow()
            db.add(acc)
            db.commit()
            db.refresh(acc)
            log.info(
                "paper account RESET audit: source=%s custom_initial=%s | "
                "before: positions=%d orders=%d cash=%s initial=%s | after: cash=%s initial=%s",
                source, initial_cash is not None,
                pos_before, ord_before, cash_before, initial_before,
                acc.cash, acc.initial_cash,
            )
            return acc

    # ---------- T+1 解冻 ----------

    async def settle_t1(self) -> int:
        """把「到期待解冻」落库（自有 session）——**只读路径的解冻入口**，返回解冻数。

        为什么必须独立于 `place_order`（R04，2026-09-14）：
        `_unfreeze` 此前**只在下单路径**被调用，而 `available = quantity - frozen_today`
        是派生值。于是一个「买入后不再有任何委托」的持仓，`frozen_today` **永不清零**
        ⇒ `available` 恒为 0 ⇒ 离场监护每轮都走
        「T+1 当日买入不可卖——下一交易日自动执行」分支，**硬止损被无限期跳过**
        （只有恰好又下过一单才顺带结算一次）。修写入路径而不修只读路径，等于没修。
        """
        with self._sf() as db:
            cleared = await self._unfreeze(db)
            if cleared:
                db.commit()
            return cleared

    async def _unfreeze(self, db: Session) -> int:
        """T+1 解冻：按**买入日锚点**判定；返回实际解冻的持仓数（不 commit，由调用方定）。

        口径（R04）：
        - **日历可用**：解冻当且仅当「买入日之后已经有过一个交易日」——
          即 `最近交易日(≤ 今天) > buy_date`。**刻意不用**旧实现的
          「今天之后的第一个交易日」，后者取的是**未来**日期，再判 `today >= 它`
          永不成立 ⇒ 日历一可用就**永不解冻**（审查 R04 已复现的正是这一条）。
        - **日历不可用**：显式降级为自然日口径 `today > buy_date` 并告警一次（按天去重）。
          该降级是**安全的**：同日买入永不满足 `today > buy_date`，红线（T+1 当日不可卖）
          不受影响；与日历口径的唯一差异是「在非交易日解冻」，而休市日无有效行情、
          `place_order` 必拒单 ⇒ 差异不可达。刻意保留而**不**"保守地永不解冻"：
          永久冻结会让硬止损彻底失效（见 `settle_t1`），那比多解冻一天危险得多。
        - **买入日缺失**：不以无锚点的日期解冻（保守），仅计数告警——
          这类行只应来自字段引入之前的历史数据。
        """
        # scope 过滤（S1-1）：解冻只作用于本账户，避免影子账户的解冻被交易账户的
        # 一次下单顺带触发（跨账户写）。语义上解冻幂等、无本金风险，但隔离边界必须一致。
        positions = db.query(PaperPosition).filter(
            PaperPosition.scope == self.scope, PaperPosition.frozen_today > 0
        ).all()
        if not positions:
            return 0
        today = _today()
        anchor = await self._last_trading_day_on_or_before(today)
        cleared = 0
        undated = 0
        for pos in positions:
            buy = _as_ymd(pos.buy_date)
            if not buy:
                undated += 1
                continue
            if (anchor > buy) if anchor else (today > buy):
                pos.frozen_today = 0
                cleared += 1
        if undated:
            log.warning(
                "T+1 解冻跳过 %d 个无有效买入日的持仓（不以无锚点的日期解冻，需人工核对）",
                undated,
            )
        return cleared

    async def _last_trading_day_on_or_before(self, today: str) -> str:
        """日历中「≤ 今天」的最近交易日（YYYYMMDD）；日历不可用返回 `""`。

        返回空串（而非 None）表示"日历不可用"：正常情况下日历必含过去一年，
        "没有 ≤ 今天的日子"只可能是拉取失败或数据形态异常，两者都应走降级而非静默。
        """
        if self._tdays_fn is None:
            self._note_t1_fallback("未注入交易日历（trading_days_fn=None）")
            return ""
        try:
            days = await self._tdays_fn()
        except Exception as exc:  # noqa: BLE001  日历是增强项，失败不得阻断下单
            self._note_t1_fallback(f"日历拉取异常 {type(exc).__name__}: {exc}")
            return ""
        norm = [d for d in (_as_ymd(x) for x in (days or [])) if d]
        past = sorted(d for d in norm if d <= today)
        if not past:
            self._note_t1_fallback(f"日历中无 ≤ {today} 的交易日（收到 {len(norm)} 项）")
            return ""
        return past[-1]

    def _note_t1_fallback(self, reason: str) -> None:
        """自然日降级告警（每 (天, scope) 一次）——降级必须可见，不能是静默行为。"""
        key = f"{_today()}:{self.scope}"
        if key in _T1_FALLBACK_LOGGED:
            return
        _T1_FALLBACK_LOGGED.add(key)
        log.warning(
            "T+1 解冻降级为自然日口径（scope=%s）：%s。"
            "同日买入仍不解冻（红线不受影响），但节假日边界不再精确",
            self.scope, reason,
        )

    # ---------- 持仓读取（scope 唯一入口） ----------

    def _position_of(self, db: Session, symbol: str) -> PaperPosition | None:
        """按 **(scope, symbol)** 取本账户持仓——卖出路径的唯一入口。

        历史缺陷（2026-09-11 S1-1）：卖出路径（`place_order` sell 分支 / `_fill_sell`）
        只按 symbol 查，而模型层是 `UniqueConstraint("scope", "symbol")` 双账户隔离
        ⇒ main 与 shadow 同持一票时 `one_or_none()` 抛 `MultipleResultsFound`
        （卖出在常驻循环里被吞、持仓卡死），单边持有时**扣错账户**。
        买入路径本就有 scope 过滤，此方法把该口径收口为单点，避免再次漏写。
        """
        return (
            db.query(PaperPosition)
            .filter(PaperPosition.scope == self.scope, PaperPosition.symbol == symbol)
            .one_or_none()
        )

    # ---------- 风控硬拦截（§6.5b #2） ----------

    def risk_check_context(self, hub) -> tuple[dict, list[dict]]:
        """风控预检的（账户, 持仓）上下文——**与 /api/risk/check-order 同口径的单点**。

        取价 = hub 全量报价，缺实时价回退成本价（绝不回退「本次订单价」——
        那会把未订阅行情的持仓按订单价计价、总仓位严重低估，见
        test_total_position_falls_back_to_cost_price_not_order_price）。
        路由与撮合层共用本方法，杜绝「UI 预检说可以、下单被拒」的口径分裂。
        """
        price_map = {q.symbol: q.price for q in hub.get_quotes() if q.price}
        positions = self.positions_with_pnl(price_map)
        for pos in positions:
            if pos["last_price"] is None and pos["symbol"] in price_map:
                last = price_map[pos["symbol"]]
                pos["last_price"] = last
                pos["pnl"] = round((last - pos["cost_price"]) * pos["quantity"], 2)
                pos["pnl_pct"] = (
                    round((last - pos["cost_price"]) / pos["cost_price"] * 100, 2)
                    if pos["cost_price"]
                    else None
                )
        market_value = sum(
            (p.get("last_price") or p.get("cost_price") or 0) * p.get("quantity", 0)
            for p in positions
        )
        account = self.account_summary(market_value)
        account["total_equity"] = account["total"]
        return account, positions

    async def _risk_block_reason(self, symbol: str, side: str, price: float, qty: int, quote) -> str | None:
        """风控硬拦截判据。返回 None = 放行，否则返回中文拒单原因。

        边界（见构造函数注释）：仅 main 账户 + 仅买入；挂单撮合期不复查。
        市场状态用的是 risk_engine 的**缓存态**（调度器每 60s 刷新，
        与 UI 预检看到的同一个值）；预检自身异常按**保守拒单**处理——
        不知道订单是否安全时，模拟盘宁可拒并说明原因（红线 2 的精神：
        失败必须可见，不静默放行）。
        """
        re_ = self._risk_engine
        # 豁免判据**写在代码里**而非只靠装配约定：scope != main 一律不闸——
        # 即使将来有人给 shadow 注入 risk_engine，研究仪器口径也不会被污染
        #（test_shadow_scope_not_gated 钉住这条结构性保证）。
        if re_ is None or side != "buy" or self.scope != "main":
            return None
        try:
            account, positions = self.risk_check_context(re_.hub)
            result = re_.check_order(
                symbol=symbol,
                side=side,
                price=price,
                quantity=qty,
                account=account,
                positions=positions,
                # 用刚取到的实时 quote（比 hub 缓存更新），quality/amount 判据同源
                quote=quote.model_dump() if quote is not None else None,
            )
        except Exception as exc:
            log.warning("risk pre-check failed for %s buy: %s", symbol, exc)
            return f"风控预检异常（{type(exc).__name__}），保守拒单"
        if result.get("allowed"):
            return None
        reason = "；".join(result.get("reasons") or []) or "未通过风控预检"
        warnings = "；".join(result.get("warnings") or [])
        return f"风控拦截：{reason}" + (f"（警告：{warnings}）" if warnings else "")

    # ---------- 下单 ----------

    async def place_order(self, symbol: str, side: str, price: float, qty: int) -> PaperOrder:
        quote = await self._quote_fn(symbol)
        order = PaperOrder(scope=self.scope, symbol=symbol, side=side, price=price, quantity=qty, status="pending")

        def reject(reason: str) -> PaperOrder:
            order.status = "rejected"
            order.reason = reason
            return order

        if not symbol.isdigit() or len(symbol) != 6:
            return reject("非法代码")
        if side not in {"buy", "sell"}:
            return reject("非法方向")
        # 数值有限性是**执行层**的硬校验，不依赖 API 层 schema（engine 也被
        # 内部调用方/脚本直接用）。见 is_valid_price 的 NaN/0/inf 三坑。
        if not is_valid_price(price):
            return reject("非法价格（须为有限正数）")
        if not isinstance(qty, int) or isinstance(qty, bool) or qty <= 0:
            return reject("非法数量")
        if quote is None or not is_valid_price(quote.price):
            return reject("停牌或无行情，无法交易")
        if side == "buy" and qty % 100 != 0:
            return reject("买入数量须为100股整数倍")

        # 涨跌停硬拦截（红线 5）：**缺限价一律拒单，不做静默放行**。
        # 限价缺失是**会发生的状态**（补价依赖链上腾讯单源，曾真实被封），
        # 且此处是模拟盘胜率基线的来源——放进一笔试不到的单子会污染统计，
        # 故取保守口径：宁可拒单并说明原因，也不放行未校验的单。
        # 顺序刻意放在账户级校验（资金/可卖量）之前：涨跌停是**市场可行性**，
        # 与用户账户状态无关，红线守卫不应因为"钱不够"而根本不被求值。
        blocked = limit_block_reason(quote, side, price)
        if blocked:
            return reject(blocked)

        with self._sf() as db:
            # 账户对象在**本 session 内**取（原实现是 session 外 `ensure_account()`
            # + `db.add()` 整行回写），且读取点后移到两次 await 之后，缩小陈旧窗口。
            acc = self._account(db)
            # 成交前结算（R04）：卖单的可卖量校验在第 381 行读 `pos.available`，
            # 而它是 `quantity - frozen_today` 的派生值——不先解冻就会把昨日买入
            # 误判成"当日买入不可卖"。
            await self._unfreeze(db)
            # 风控硬拦截（§6.5b #2）：仅 main 买入。放在账户级资金校验之前——
            # 与涨跌停拦截同理：「该不该买」不应因「买不买得起」不满足而不被求值。
            risk_blocked = await self._risk_block_reason(symbol, side, price, qty, quote)
            if risk_blocked:
                return reject(risk_blocked)
            if side == "buy":
                need = price * qty + calc_fee("buy", price, qty)
                if need > acc.cash:
                    return reject(f"可用资金不足（需 {need:.0f}，余 {acc.cash:.0f}）")
                # 限价撮合：买价 >= 现价 → 按现价成交；否则挂单
                if price >= quote.price:
                    return await self._fill(db, acc, order, quote.price, qty)
                acc.cash -= need  # 挂单冻结
                db.add(order)
                db.add(acc)
                db.commit()
                db.refresh(order)
                return order
            # sell
            pos = self._position_of(db, symbol)
            if pos is None or pos.available < qty:
                avail = pos.available if pos else 0
                return reject(f"可卖数量不足（{avail}）")
            if price <= quote.price:
                return await self._fill_sell(db, acc, order, quote.price, qty)
            db.add(order)
            db.commit()
            db.refresh(order)
            return order

    # ---------- 成交（买） ----------

    async def _fill(self, db: Session, acc: PaperAccount, order: PaperOrder, fill_price: float, qty: int) -> PaperOrder:
        # 成交价二次校验（R05）：本方法被 `match_pending` 直接调用，不经
        # `place_order` 的价格校验。挂单期间的行情可能变成 0/NaN/inf
        # （上游缺值被填 0 是常见形态），此时按 0 元成交等于白送持仓。
        if not is_valid_price(fill_price):
            order.status = "rejected"
            order.reason = f"成交价无效（{fill_price!r}），拒单"
            db.add(order)
            db.commit()
            db.refresh(order)
            return order
        fee = calc_fee("buy", fill_price, qty)
        cost = fill_price * qty + fee
        if cost > acc.cash:
            order.status = "rejected"
            order.reason = "可用资金不足（含费用）"
            db.add(order)
            db.commit()
            return order
        acc.cash -= cost
        pos = db.query(PaperPosition).filter(
            PaperPosition.scope == self.scope, PaperPosition.symbol == order.symbol
        ).one_or_none()
        today = _today()
        if pos is None:
            pos = PaperPosition(scope=self.scope, symbol=order.symbol, quantity=0, frozen_today=0, cost_price=0, buy_date=today)
            db.add(pos)
        total_cost = pos.cost_price * pos.quantity + fill_price * qty
        pos.quantity += qty
        pos.cost_price = round(total_cost / pos.quantity, 4)
        pos.frozen_today += qty  # T+1
        pos.buy_date = today
        order.status = "filled"
        order.filled_price = fill_price
        order.fee = fee
        db.add(acc)
        db.add(pos)
        db.add(order)
        db.commit()
        db.refresh(order)
        return order

    # ---------- 成交（卖） ----------

    async def _fill_sell(self, db: Session, acc: PaperAccount, order: PaperOrder, fill_price: float, qty: int) -> PaperOrder:
        # 成交价二次校验（R05）：与 `_fill` 同源——卖出按 0 元成交会凭空
        # 交出持仓且分文不入（proceeds 被费用吃成负数）。
        if not is_valid_price(fill_price):
            order.status = "rejected"
            order.reason = f"成交价无效（{fill_price!r}），拒单"
            db.add(order)
            db.commit()
            db.refresh(order)
            return order
        pos = self._position_of(db, order.symbol)
        # 成交前二次校验：`match_pending` 直接调用本方法，不经 place_order 的可卖量校验。
        # 无此校验时 quantity 会被扣成负数，又被下面 `quantity <= 0` 分支静默删除 ⇒
        # 持仓消失但卖出款已入账（同一批可用量可被两次挂单重复卖出）。S1-1 一并加固。
        if pos is None or pos.available < qty:
            order.status = "rejected"
            order.reason = f"可卖数量不足（成交时校验：{pos.available if pos else 0}）"
            db.add(order)
            db.commit()
            db.refresh(order)
            return order
        fee = calc_fee("sell", fill_price, qty)
        proceeds = fill_price * qty - fee
        acc.cash += proceeds
        pos.quantity -= qty
        if pos.quantity <= 0:
            db.delete(pos)
        order.status = "filled"
        order.filled_price = fill_price
        order.fee = fee
        db.add(acc)
        db.add(order)
        db.commit()
        db.refresh(order)
        return order

    # ---------- 挂单轮询撮合 ----------

    async def match_pending(self) -> int:
        """对全部挂单重试撮合（价格到位即成交）。返回**剩余挂单数**（技术债 #5：
        调用方据此自适应降频——无挂单时空转降频，有挂单才密集轮询）。"""
        with self._sf() as db:
            pending = db.query(PaperOrder).filter(
                PaperOrder.status == "pending", PaperOrder.scope == self.scope
            ).all()
            symbols = {o.symbol for o in pending}
            pending_left = len(pending)
        if not symbols:
            return pending_left
        for sym in symbols:
            try:
                quote = await self._quote_fn(sym)
            except Exception:
                continue
            if not is_valid_price(getattr(quote, "price", None)):
                # R05：原判据只有 `quote.price is None`，价格为 **0** 时会进来
                # 并按 0 元撮合成交（现金只入卖出费用差额 / 买入白得持仓）。
                # 无效价一律跳过本轮，挂单保留待下一轮（行情恢复后可正常成交）。
                log.warning("挂单撮合跳过 %s：行情价无效（%r）", sym, getattr(quote, "price", None))
                continue
            with self._sf() as db:
                for o in db.query(PaperOrder).filter(
                    PaperOrder.status == "pending", PaperOrder.scope == self.scope,
                    PaperOrder.symbol == sym,
                ).all():
                    acc = self._account(db)
                    # 成交前二次校验涨跌停（红线 5）：挂单可能是在股价冲上涨停**之前**
                    # 挂下的，`place_order` 的那次校验代表不了此刻。缺限价时同样不成交
                    # ——静默按现价成交等于在涨停板买入/在跌停板卖出。
                    blocked = limit_block_reason(quote, o.side, quote.price)
                    if blocked:
                        log.info("挂单暂不撮合 %s %s：%s", o.side, sym, blocked)
                        continue
                    if o.side == "buy" and o.price >= quote.price:
                        # 先归还下单时的冻结额，再由 `_fill` 扣本次实付。二者配对，
                        # 缺一即**重复扣款**（冻结额永不归还）——见 buy_freeze_amount。
                        # 释放对「被拒单」同样必要：`_fill` 若因资金不足拒单，
                        # 用户应拿回冻结额，而不是钱被扣掉且单子作废。
                        acc.cash += buy_freeze_amount(o.price, o.quantity)
                        await self._fill(db, acc, o, quote.price, o.quantity)
                    elif o.side == "sell" and o.price <= quote.price:
                        await self._fill_sell(db, acc, o, quote.price, o.quantity)
        with self._sf() as db:
            pending_left = db.query(PaperOrder).filter(
                PaperOrder.status == "pending", PaperOrder.scope == self.scope
            ).count()
        return pending_left

    # ---------- 撤单 ----------

    def cancel(self, order_id: int) -> PaperOrder | None:
        with self._sf() as db:
            o = db.query(PaperOrder).filter(
                PaperOrder.id == order_id, PaperOrder.status == "pending",
                PaperOrder.scope == self.scope,
            ).one_or_none()
            if o is None:
                return None
            o.status = "cancelled"
            if o.side == "buy":  # 解冻资金（含预扣佣金）——与 match_pending 同口径
                acc = self._account(db)
                acc.cash += buy_freeze_amount(o.price, o.quantity)
                db.add(acc)
            db.add(o)
            db.commit()
            db.refresh(o)
            return o

    # ---------- 持仓快照（含市值盈亏） ----------

    def positions_with_pnl(self, price_map: dict[str, float]) -> list[dict]:
        with self._sf() as db:
            positions = db.query(PaperPosition).filter(PaperPosition.scope == self.scope).all()
            out = []
            for pos in positions:
                last = price_map.get(pos.symbol)
                pnl = None if last is None else round((last - pos.cost_price) * pos.quantity, 2)
                pnl_pct = None if (last is None or pos.cost_price == 0) else round((last - pos.cost_price) / pos.cost_price * 100, 2)
                out.append({
                    "symbol": pos.symbol,
                    "quantity": pos.quantity,
                    "available": pos.available,
                    "cost_price": pos.cost_price,
                    "last_price": last,
                    "pnl": pnl,
                    "pnl_pct": pnl_pct,
                })
            return out

    def frozen_cash(self) -> float:
        """本账户当前**被挂单占用的资金**（含预扣佣金）。

        买入挂单时 `place_order` 直接从 `acc.cash` 扣掉冻结额（`cash` 语义 =
        **可用资金**），但这笔钱在撤单前仍是账户的资产——它只是"不能动"，
        不是"已经花掉"。缺了这项，一挂上限价单账户总权益就凭空少一截。
        """
        with self._sf() as db:
            rows = db.query(PaperOrder).filter(
                PaperOrder.scope == self.scope,
                PaperOrder.status == "pending",
                PaperOrder.side == "buy",
            ).all()
            return round(sum(buy_freeze_amount(o.price, o.quantity) for o in rows), 2)

    def pending_orders(self, side: str | None = None) -> list[dict]:
        """未成交挂单（scope 隔离；不含已成交/已拒/已撤）。

        R09（2026-09-14）：仓位引擎据此**占用持仓名额并计入敞口**。此前判据只看
        `positions_with_pnl`，未决挂单既不占名额也不计敞口 ⇒ 同一触发可在撮合前
        对同一只票反复挂单，`MAX_POS` 与总敞口约束同时失效。
        与 `frozen_cash` 同源：挂单占用的是「**资金 + 名额**」，两者必须一起看。

        注意本方法**不吞异常**（与 `frozen_cash` 一致）：读不到挂单时调用方
        必须 fail-closed，不得把「读失败」当作「没有挂单」。
        """
        with self._sf() as db:
            q = db.query(PaperOrder).filter(
                PaperOrder.scope == self.scope, PaperOrder.status == "pending"
            )
            if side:
                q = q.filter(PaperOrder.side == side)
            return [
                {"symbol": o.symbol, "side": o.side, "price": o.price, "quantity": o.quantity}
                for o in q.order_by(PaperOrder.id).all()
            ]

    def account_summary(self, market_value: float) -> dict:
        """账户摘要。`cash` = **可用资金**；`frozen_cash` = 挂单占用；`total` = 净资产。

        R01 残留项修复（2026-09-14）：原 `total = cash + market_value`，
        而挂单冻结额已经从 `cash` 里扣走、又不在持仓市值内 ⇒ 一挂上限价单，
        **总权益就凭空下降**，`total_pnl` / 回撤保护判据跟着一起错。
        合成场景（限价 95 挂 1000 股、现价 100）实测权益由 100 万虚降为
        904,975.30 —— 用户看到的"今天亏了 9 万"其实是自己的挂单。

        三者关系：`total = cash(可用) + frozen_cash(冻结) + market_value(持仓市值)`。
        """
        acc = self.ensure_account()
        frozen = self.frozen_cash()
        total = acc.cash + frozen + market_value
        return {
            "cash": round(acc.cash, 2),
            "frozen_cash": frozen,
            "market_value": round(market_value, 2),
            "total": round(total, 2),
            # 回撤保护依赖此项：缺它时风控会把 initial 退回 equity，pnl 恒为 0、保护永不触发
            "initial_cash": round(acc.initial_cash, 2),
            "total_pnl": round(total - acc.initial_cash, 2),
            "total_pnl_pct": round((total - acc.initial_cash) / acc.initial_cash * 100, 2),
        }
