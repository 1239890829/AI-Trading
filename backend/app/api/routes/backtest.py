"""日线回测端点（Phase 6 后半）。

POST /backtest/run：同步计算（500 根日K秒级完成，无需缓存）。
强制禁令见 docs/backtest-rules.md——引擎内为代码级校验，防泄露测试先行合入。
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.core.errors import AppError
from app.market.backtest import (
    STRATEGY_REGISTRY,
    BacktestConfig,
    build_strategy,
    run_backtest,
)
from app.schemas.backtest import (
    BacktestEquityPoint,
    BacktestMetrics,
    BacktestPayload,
    BacktestTrade,
)
from app.schemas.envelope import Envelope

router = APIRouter(tags=["backtest"])


class BacktestRunRequest(BaseModel):
    symbol: str
    strategy_id: str
    params: dict = Field(default_factory=dict)
    bars: int = Field(default=500, ge=100, le=500, description="回看日K根数")


@router.post("/backtest/run", response_model=Envelope[BacktestPayload])
async def run_symbol_backtest(req: BacktestRunRequest) -> dict:
    """单标的日线策略回测（TDX QFQ 日K）。

    报告含基准对比/超额/最大回撤/夏普系/胜率/盈亏比与样本内外分离
    （docs/backtest-rules.md §5）；结果为统计事实，不构成买卖建议。
    """
    symbol = req.symbol.strip().zfill(6)
    if not symbol.isdigit() or len(symbol) != 6:
        raise AppError(f"非法代码：{req.symbol}", code="validation_error", status_code=400)
    try:
        strategy = build_strategy(req.strategy_id, req.params)
    except ValueError as exc:
        raise AppError(str(exc), code="validation_error", status_code=400) from exc

    from app.market.tdx_kline import tdx_daily_bars

    bars = tdx_daily_bars(symbol, count=req.bars)
    if not bars or len(bars) < 60:
        raise AppError(
            f"{symbol} 日K数据不足（拿到 {len(bars) if bars else 0} 根）",
            code="data_insufficient", status_code=502,
        )
    try:
        report = run_backtest(bars, strategy, BacktestConfig())
    except ValueError as exc:
        raise AppError(str(exc), code="backtest_failed", status_code=400) from exc

    payload = BacktestPayload(
        symbol=symbol,
        strategy_id=req.strategy_id,
        bars_count=len(bars),
        metrics=BacktestMetrics(
            total_return=report.total_return,
            benchmark_return=report.benchmark_return,
            excess_return=report.excess_return,
            annual_return=report.annual_return,
            max_drawdown=report.max_drawdown,
            max_drawdown_days=report.max_drawdown_days,
            sharpe=report.sharpe,
            sortino=report.sortino,
            calmar=report.calmar,
            win_rate=report.win_rate,
            profit_loss_ratio=report.profit_loss_ratio,
            in_return=report.in_return,
            out_return=report.out_return,
        ),
        equity=[
            BacktestEquityPoint(ts=ts, value=v, benchmark=b)
            for ts, v, b in zip(report.equity_ts, report.equity, report.benchmark)
        ],
        trades=[
            BacktestTrade(
                signal_ts=t.signal_ts, fill_ts=t.fill_ts, side=t.side,
                price=round(t.price, 4), ref_price=t.ref_price, qty=t.qty,
                fee=t.fee, ok=t.ok, reason=t.reason,
            )
            for t in report.trades
        ],
        config=report.config,
        notes=report.notes,
    )
    return {"data": payload, "meta": {}}


@router.get("/backtest/strategies", response_model=Envelope[list])
async def list_strategies() -> dict:
    """可用策略清单（id/名称/默认参数）。"""
    items = [
        {"id": sid, "name": e["name"], "params": e["params"]}
        for sid, e in STRATEGY_REGISTRY.items()
    ]
    return {"data": items, "meta": {}}
