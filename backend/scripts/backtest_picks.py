"""选股 2.0 批次 D：确认规则网格回测跑批（§8 协议）。

流程（复用 app/picks/backtest.py 的同一份规则代码回放确认规则）：
1. 交易日窗口：官方日历截至"今天之前"取尾部 days+1 天（T 需要 T-1）；
2. 逐日拉 ths 涨停池（0.15s 间隔防配额打满，identical-pool 哨兵防静默回退）；
3. T-1 题材 top3 为伪预判 → §5.1 确认规则收盘口径回放（阈值经覆盖参数注入网格）；
4. 收益口径：板块指数 T+1 开盘买、T+3 收盘卖；网格 涨幅 1.0/1.5/2.0/2.5 × 量比 1.3/1.5/2.0。

用法：
  cd backend && .venv/bin/python scripts/backtest_picks.py                 # 120 交易日
  cd backend && .venv/bin/python scripts/backtest_picks.py --days 200      # 扩窗复验
  cd backend && .venv/bin/python scripts/backtest_picks.py --out docs/x.md

报告输出：docs/stock-picking-backtest-<date>.md + stdout。
纪律：报告只给建议，**不自动回写** intraday_rules 常量（§8 防过拟合，
回写须人工拍板并换窗口复验）。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings
from app.data_providers.ths import ThsFuyaoProvider
from app.picks import backtest as bt
from app.services.theme_catalog_service import ThemeCatalogService

ROOT = Path(__file__).resolve().parent.parent.parent


async def main(days: int, out: Path | None) -> int:
    provider = ThsFuyaoProvider(api_key=settings.ths_api_key)
    # session_factory=None：跑批只用 fetch_catalog/fetch_board_bars，不碰 DB
    svc = ThemeCatalogService(session_factory=None)
    try:
        report = await bt.run_backtest(
            days=days,
            provider=provider,
            catalog_fetcher=svc.fetch_catalog,
            bars_fetcher=svc.fetch_board_bars,
        )
    finally:
        await provider._client.aclose()
        await svc.aclose()
    target = out or (ROOT / "docs" / f"stock-picking-backtest-{date.today().isoformat()}.md")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(report, encoding="utf-8")
    print(report)
    print(f"\n[report saved] {target}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="选股 2.0 确认规则网格回测")
    ap.add_argument("--days", type=int, default=120, help="回测交易日数（协议要求 ≥120）")
    ap.add_argument("--out", type=Path, default=None, help="报告输出路径（默认 docs/ 下按日期命名）")
    a = ap.parse_args()
    raise SystemExit(asyncio.run(main(a.days, a.out)))
