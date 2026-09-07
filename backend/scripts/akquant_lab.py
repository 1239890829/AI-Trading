"""akquant 策略实验室：marketdb 日K → akquant.run_backtest 通路验证（docs/ai-brain-plan.md P0）。

用途：验证「本地 DuckDB 日K仓 → akquant 回测」数据直通，为「选股规则回测化」
（P2 批次）打通地基。与既有 scripts/backtest_picks.py（确认规则网格，走 ths
板块口径）互不替代：本脚本面向个股 OHLCV 规则回放。

运行：
  cd backend && .venv/bin/python scripts/akquant_lab.py                    # 默认标的
  cd backend && .venv/bin/python scripts/akquant_lab.py --symbol 300308.SZ

依赖：akquant（requirements.lock 已含）；marketdb 未建仓时提示先 sync_marketdb。
"""
from __future__ import annotations

import argparse
import contextlib
import io
import sys
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "marketdb" / "market.duckdb"


def load_daily_k(symbol: str):
    """从 marketdb 读单只股票全部日 K，rename 成 akquant 规范列。"""
    import duckdb
    import pandas as pd

    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        df = con.execute(
            "select date_ms, open_price as open, high_price as high, low_price as low, "
            "close_price as close, volume from daily_k where thscode = ? order by date_ms",
            [symbol],
        ).fetchdf()
    finally:
        con.close()
    if df.empty:
        raise SystemExit(f"marketdb 无 {symbol} 数据；先跑 scripts/sync_marketdb.py")
    df["date"] = pd.to_datetime(df["date_ms"], unit="ms").dt.tz_localize(None)
    return df.drop(columns=["date_ms"])


def run(symbol: str) -> dict:
    import akquant as aq

    df = load_daily_k(symbol)

    class MomentumFlip(aq.Strategy):
        """演示策略：空仓遇阳线买入 100 股、持仓遇阴线清仓（最简可复现规则）。"""

        def on_bar(self, bar):
            pos = self.get_position(bar.symbol)
            if pos == 0 and bar.close > bar.open:
                self.buy(symbol=bar.symbol, quantity=100)
            elif pos > 0 and bar.close < bar.open:
                self.close_position(symbol=bar.symbol)

    with contextlib.redirect_stdout(io.StringIO()):  # 静音回测进度条
        result = aq.run_backtest(
            data=df[["date", "open", "high", "low", "close", "volume"]],
            strategy=MomentumFlip,
            initial_cash=100_000.0,
        )
    # metrics 是 Rust 动态转发对象（dir 不枚举、无 closed_trade_count 属性），
    # 可靠口径：交易数用 get_trades_dict()，收益率用 metrics_df
    trades = result.get_trades_dict()
    # metrics_df：index=指标名，value 列=值（249 bars 实测结构）
    mdf = result.metrics_df
    total_ret = float(mdf.loc["total_return_pct", "value"]) if "total_return_pct" in mdf.index else 0.0
    return {
        "symbol": symbol,
        "bars": len(df),
        "first": str(df["date"].iloc[0].date()),
        "last": str(df["date"].iloc[-1].date()),
        "closed_trades": len(trades),
        "total_return_pct": total_ret,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="akquant × marketdb 回测通路验证")
    parser.add_argument("--symbol", default="000910.SZ", help="marketdb thscode（带交易所后缀）")
    args = parser.parse_args()

    try:
        import akquant as _aq  # 可用性探测（未装时走 2 退出码）
    except ImportError:
        print("akquant 未安装：backend/.venv/bin/pip install akquant", file=sys.stderr)
        return 2
    del _aq
    if not DB_PATH.exists():
        print(f"marketdb 不存在：{DB_PATH}", file=sys.stderr)
        return 2

    s = run(args.symbol)
    print(
        f"[akquant-lab] {s['symbol']} {s['first']}→{s['last']} bars={s['bars']} "
        f"closed_trades={s['closed_trades']} total_return={s['total_return_pct']:.2f}%"
    )
    print("OK: marketdb → akquant 通路可用")
    return 0


if __name__ == "__main__":
    sys.exit(main())
