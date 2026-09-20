"""TDX 逐笔 第四轮：手动分页正确性 + 东财并排对照 + 成交量交叉验证。"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import httpx
import pandas as pd
from easy_tdx import MacClient, Market

MKT = Market.SH.value
CODE = "600519"


def tstr(s: pd.Series) -> list[str]:
    return [v.strftime("%H:%M:%S") if isinstance(v, dt.time) else str(v) for v in s]


def tdx_full_day(client, code: str, market: int, ymd: int | None = None, page: int = 1000):
    """手动分页：start 以「最新」为原点，段内升序 ⇒ 后取的页要 insert 到前面。"""
    pages: list[pd.DataFrame] = []
    start = 0
    while True:
        df = client.get_transactions(market, code, count=page, start=start, date=ymd)
        if df is None or len(df) == 0:
            break
        pages.insert(0, df)
        if len(df) < page:
            break
        start += len(df)
        if start > 60000:
            break
    if not pages:
        return None
    return pd.concat(pages, ignore_index=True)


def main() -> None:
    with MacClient() as client:
        print("=" * 72)
        print("L) 手动分页取全天（600519.SH 当日）")
        print("=" * 72)
        df = tdx_full_day(client, CODE, MKT)
        if df is None:
            print("空")
        else:
            t = pd.Series(tstr(df["time"]))
            print(f"rows={len(df)} min={t.min()} max={t.max()} mono_inc={t.is_monotonic_increasing}")
            print(f"Σvol(手)={float(df['vol'].sum()):,.0f}  Σtrade_count={int(df['trade_count'].sum()):,}")
            print(f"金额估算(price*vol*100)={float((df['price']*df['vol']*100).sum())/1e8:.2f} 亿")
            print("bs_flag 分布:", df["bs_flag"].value_counts().sort_index().to_dict())
            print("首3:", tstr(df["time"])[:3], " 末3:", tstr(df["time"])[-3:])

        print()
        print("=" * 72)
        print("M) 历史日 9/15 手动分页")
        print("=" * 72)
        d15 = tdx_full_day(client, CODE, MKT, ymd=20260915)
        if d15 is not None:
            t = pd.Series(tstr(d15["time"]))
            print(f"rows={len(d15)} min={t.min()} max={t.max()} mono_inc={t.is_monotonic_increasing}")
            print(f"Σvol(手)={float(d15['vol'].sum()):,.0f} Σtrade_count={int(d15['trade_count'].sum()):,}")

        print()
        print("=" * 72)
        print("N) 分时 get_tick_chart 的 Σvol 对照（同源自洽性）")
        print("=" * 72)
        for ymd in (None, 20260915):
            tc = client.get_tick_chart(MKT, CODE, date=ymd)
            if tc is not None and len(tc):
                print(f"date={ymd}: rows={len(tc)} Σvol={float(tc['vol'].sum()):,.0f} 手")

    print()
    print("=" * 72)
    print("O) 东财 details 实测（本机连通性 + 当日条数）")
    print("=" * 72)
    try:
        r = httpx.get(
            "https://push2his.eastmoney.com/api/qt/stock/details/get",
            params={
                "secid": "1.600519",
                "fields1": "f1,f2,f3,f4,f5",
                "fields2": "f51,f52,f53,f54,f55",
                "pos": "-100",
            },
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"},
            timeout=15.0,
        )
        print("HTTP", r.status_code, "len", len(r.content))
        j = r.json()
        d = (j.get("data") or {})
        det = d.get("details") or []
        print("rc=", j.get("rc"), "data keys=", list(d.keys()), "details rows=", len(det))
        print("首3:", det[:3])
        print("末3:", det[-3:])
    except Exception as e:  # noqa: BLE001
        print("EXC", type(e).__name__, e)

    print()
    print("=" * 72)
    print("P) marketdb 当日成交量对照（600519 9/16、9/15）")
    print("=" * 72)
    try:
        import duckdb

        db = Path(__file__).resolve().parents[3] / "backend/data/marketdb/market.duckdb"
        con = duckdb.connect(str(db), read_only=True)
        q = con.execute(
            "select ts, open, high, low, close, volume, amount from daily_k "
            "where symbol='600519' and ts >= '2026-09-14' order by ts"
        ).fetchall()
        for row in q:
            print(row)
        con.close()
    except Exception as e:  # noqa: BLE001
        print("EXC", type(e).__name__, e)


if __name__ == "__main__":
    main()
