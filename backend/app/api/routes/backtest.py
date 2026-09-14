"""日线回测端点（Phase 6 后半）。

POST /backtest/run：同步计算（500 根日K秒级完成，无需缓存）。
强制禁令见 docs/backtest-rules.md——引擎内为代码级校验，防泄露测试先行合入。

/walkforward 端点已删（2026-09-08 审查 P0-4：前端零调用、/scripts 零引用；
walk-forward 门禁列为 P2 数据积累项，届时随 IC 跑批管线一起落地，
git 历史可恢复 engine 实现 app/market/walkforward.py）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.deps import require_write_token
from app.core.errors import AppError
from app.market.backtest import (
    STRATEGY_REGISTRY,
    build_strategy,
    run_backtest,
)
from app.schemas.backtest import (
    BacktestEquityPoint,
    BacktestMetrics,
    BacktestOpenPosition,
    BacktestPayload,
    BacktestTrade,
)
from app.schemas.envelope import Envelope

router = APIRouter(tags=["backtest"])


class BacktestRunRequest(BaseModel):
    # 全部可选：给了 mandate 时可省略，由 mandate 提供；显式字段优先级高于 mandate
    symbol: str | None = None
    strategy_id: str | None = None
    params: dict | None = None
    bars: int | None = Field(default=None, ge=100, le=500, description="回看日K根数")
    mandate: str | None = Field(default=None, description="mandate 文件名（不含 .yaml）")


@router.post("/backtest/run", response_model=Envelope[BacktestPayload], dependencies=[Depends(require_write_token)])
async def run_symbol_backtest(req: BacktestRunRequest) -> dict:
    """单标的日线策略回测（TDX QFQ 日K）。

    参数解析分层：代码默认 < mandate 文件 < 请求显式字段；
    结果 meta.applied 逐字段说明来源（可解释，不做黑箱合并）。
    报告含基准对比/超额/最大回撤/夏普系/胜率/盈亏比与样本内外分离
    （docs/backtest-rules.md §5）；结果为统计事实，不构成买卖建议。
    """
    from app.market.mandate import resolve_backtest_request

    try:
        r = resolve_backtest_request(
            symbol=req.symbol, strategy_id=req.strategy_id, params=req.params,
            bars=req.bars, mandate_name=req.mandate,
        )
    except ValueError as exc:
        raise AppError(str(exc), code="validation_error", status_code=400) from exc

    symbol = r.symbol
    try:
        strategy = build_strategy(r.strategy_id, r.params)
    except ValueError as exc:
        raise AppError(str(exc), code="validation_error", status_code=400) from exc

    from app.market.tdx_kline import tdx_daily_bars

    bars = tdx_daily_bars(symbol, count=r.bars)
    if not bars or len(bars) < 60:
        raise AppError(
            f"{symbol} 日K数据不足（拿到 {len(bars) if bars else 0} 根）",
            code="data_insufficient", status_code=502,
        )
    try:
        report = run_backtest(bars, strategy, r.config)
    except ValueError as exc:
        raise AppError(str(exc), code="backtest_failed", status_code=400) from exc

    payload = BacktestPayload(
        symbol=symbol,
        strategy_id=r.strategy_id,
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
                fee=t.fee, ok=t.ok, reason=t.reason, synthetic=t.synthetic,
            )
            for t in report.trades
        ],
        config=report.config,
        metrics_extra=report.extra_metrics,
        notes=report.notes,
        open_position=(
            BacktestOpenPosition(**vars(report.open_position))
            if report.open_position is not None else None
        ),
    )
    return {"data": payload, "meta": {"applied": r.applied, "mandate": r.mandate}}


@router.get("/backtest/mandates", response_model=Envelope[list])
async def list_mandates() -> dict:
    """可用回测 mandate 清单（yaml 声明文件，backend/mandates/）。"""
    from app.market.mandate import list_mandates as _list

    return {"data": _list(), "meta": {}}


@router.get("/backtest/strategies", response_model=Envelope[list])
async def list_strategies() -> dict:
    """可用策略清单（id/名称/默认参数）。"""
    items = [
        {"id": sid, "name": e["name"], "params": e["params"]}
        for sid, e in STRATEGY_REGISTRY.items()
    ]
    return {"data": items, "meta": {}}
