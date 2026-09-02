"""复盘编排：把采集、分析、合成、落库串起来。

编排刻意保持**线性且无隐藏分支**：每一步的失败都必须体现在报告里
（`DataGap` / `status=blocked`），而不是让整个复盘静默失败。
"今天没跑出报告"比"跑出了缺数据的报告"更难排查。

任务顺序：
    1. 交易日解析（trade_calendar，绝不猜）
    2. 数据采集（市场 + 交易，并发）
    3. 模型路由与分析（失败降级）
    4. 改进项合成
    5. 元结论生成
    6. 持久化
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date

from app.market import trade_calendar as tc
from app.review.analyzers import LLMAnalyzer
from app.review.collector import collect_market, collect_trading
from app.review.config import ensure_default_methodology_file, load_methodology
from app.review.model_router import ModelRouter
from app.review.schemas import ReviewData, ReviewReport
from app.review.storage import save_report
from app.review.synthesis import build_action_items

log = logging.getLogger(__name__)


class ReviewService:
    def __init__(
        self,
        hub,
        snapshot_service,
        session_factory,
        *,
        model: str = "rules",
        llm_base_url: str = "",
        llm_api_key: str = "",
        llm_model: str = "",
        methodology_version: str = "v1",
        state=None,
    ):
        self.hub = hub
        self.snapshot_service = snapshot_service
        self.session_factory = session_factory
        self.methodology_version = methodology_version
        #: app.state 引用（可选）：运行时惰性取 ths_sentinel——哨兵实例在
        #: lifespan 后段才创建，构造时不存在，不能在 __init__ 里取。
        self.state = state
        self._router = ModelRouter(
            requested=model,
            llm=LLMAnalyzer(base_url=llm_base_url, api_key=llm_api_key, model=llm_model),
        )
        ensure_default_methodology_file()

    async def resolve_trade_date(self, trade_date: date | None = None) -> date:
        """确定复盘交易日。

        **绝不猜日期**：东财涨停池对非交易日静默回退到最近交易日，
        靠 `date.today()` 推算会产生自指计算（2026-08-29 事故）。
        """
        if trade_date is not None:
            return trade_date
        days = await tc.trading_days(self.hub.provider)
        anchor = tc.last_trade_date(days)
        if not anchor:
            raise RuntimeError("交易日历不可用，无法确定复盘交易日")
        return anchor

    def _price_map(self) -> dict[str, float]:
        """从全市场快照构造 symbol → 最新价。"""
        out: dict[str, float] = {}
        for row in self.snapshot_service.snapshot or []:
            sym = row.get("symbol")
            price = row.get("price")
            if sym and price:
                out[str(sym)] = float(price)
        return out

    async def run(self, trade_date: date | None = None,
                  methodology_version: str | None = None) -> ReviewReport:
        version = methodology_version or self.methodology_version
        method = load_methodology(version)

        anchor = await self.resolve_trade_date(trade_date)

        # --- 采集：市场（异步 IO）与交易（DB 同步）并行 ---
        sentinel = getattr(self.state, "ths_sentinel", None) if self.state else None
        market_coro = collect_market(
            self.hub, self.snapshot_service, anchor, sentinel=sentinel
        )
        trading_task = asyncio.to_thread(
            collect_trading, self.session_factory, anchor, self._price_map()
        )
        try:
            market, trading = await asyncio.gather(market_coro, trading_task)
        except Exception:
            log.exception("review collection failed")
            raise

        data = ReviewData(
            trade_date=anchor.strftime("%Y%m%d"), market=market, trading=trading
        )

        # --- 分析 ---
        # LLM 接入后这里是同步 HTTP（最长 30s），必须丢线程池，
        # 否则会卡住事件循环上所有的轮询与行情推送
        dimensions, usage = await asyncio.to_thread(self._router.analyze, data, method)

        # --- 合成改进项与元结论 ---
        action_items = build_action_items(data, dimensions, method)
        from app.review.methodology import build_meta_insights

        meta_insights = build_meta_insights(data, dimensions, method)

        # --- 预判验证钩子：存在针对本交易日的 pending 预判则自动回填四问 ---
        predict_note = None
        try:
            from app.predict.service import maybe_auto_verify

            predict_note = await maybe_auto_verify(
                self.hub, self.snapshot_service, self.session_factory, anchor
            )
        except Exception:
            log.exception("prediction auto-verify failed")

        summary = self._summarize(dimensions, action_items, data)
        if predict_note:
            summary = f"{summary}；{predict_note}"

        report = ReviewReport(
            trade_date=data.trade_date,
            methodology_version=version,
            model=usage,
            data=data,
            dimensions=dimensions,
            action_items=action_items,
            meta_insights=meta_insights,
            summary=summary,
        )
        return save_report(self.session_factory, report)

    @staticmethod
    def _summarize(dimensions, action_items, data: ReviewData) -> str:
        parts: list[str] = []
        phase = (data.market.sentiment or {}).get("phase")
        if phase:
            parts.append(f"情绪 {phase}")
        parts.append(f"委托 {data.trading.trade_count} 笔")
        if data.trading.realized_pnl is not None:
            parts.append(f"已实现 {data.trading.realized_pnl:+.2f}")
        gaps = len(data.all_gaps)
        if gaps:
            parts.append(f"数据缺失 {gaps} 处")
        p0 = [i for i in action_items if i.priority == "P0"]
        if p0:
            parts.append(f"P0 改进项 {len(p0)} 条")
        judged = sum(len(d.judgements) for d in dimensions)
        parts.append(f"判断 {judged} 条")
        return "；".join(parts)


# ---------------------------------------------------------------- 调度


async def review_scheduler(
    service: "ReviewService",
    *,
    run_hour: int = 15,
    run_minute: int = 30,
    check_interval_seconds: float = 60.0,
    stop: asyncio.Event | None = None,
):
    """收盘后自动触发复盘。

    判定条件（全部满足才跑）：
    1. 今天是交易日（走 trade_calendar，不用 weekday 猜）
    2. 当前北京时间已过 run_hour:run_minute
    3. 今天还没跑过（查库里有没有当日报告，**不能**用内存变量去重）

    第 3 条必须持久化：内存变量在进程重启后清空，而"时间已过触发点"这个条件
    在重启后天然成立 → 每次重启都重跑当日复盘。重跑会生成新的 review_id，
    是孤儿改进项行的主要来源（2026-09-01 实测：7 条真实改进项累积成 113 行）。

    非交易日或时间未到就跳过，**不补跑历史日期**——
    补跑会让人分不清"这份报告是哪天生成的"。要补跑请用手动触发接口。
    """
    from datetime import datetime, timedelta, timezone

    from app.review.storage import report_exists

    cst = timezone(timedelta(hours=8))
    stop = stop or asyncio.Event()

    log.info("review scheduler started: daily %02d:%02d CST", run_hour, run_minute)
    while not stop.is_set():
        try:
            now = datetime.now(cst)
            today = now.date()
            ymd = today.strftime("%Y%m%d")
            if (now.hour, now.minute) >= (run_hour, run_minute):
                days = await tc.trading_days(service.hub.provider)
                if tc.is_trade_day(days, today) and not report_exists(service.session_factory, ymd):
                    log.info("review scheduler trigger: %s", today)
                    try:
                        await service.run(today)
                    except Exception:
                        log.exception("scheduled review failed: %s", today)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("review scheduler tick failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=check_interval_seconds)
        except asyncio.TimeoutError:
            pass
        except asyncio.CancelledError:
            raise
