"""TDX 逐笔成交可用性与口径实测（只读探针，IMP-038 落地前取证）。"""
from __future__ import annotations

import traceback

from easy_tdx import MacClient, Market

SYMBOL = "600519"
MKT = Market.SH.value


def sep(t: str) -> None:
    print("\n" + "=" * 72)
    print(t)
    print("=" * 72)


def probe_transactions(client) -> None:
    sep("1) get_transactions 当日（date=None）")
    df = client.get_transactions(MKT, SYMBOL)
    print("type:", type(df))
    print("shape:", None if df is None else df.shape)
    if df is None or len(df) == 0:
        print("!! 空返回")
        return
    print("columns:", list(df.columns))
    print("dtypes:\n", df.dtypes)
    print("head(3):\n", df.head(3).to_string())
    print("tail(3):\n", df.tail(3).to_string())
    print("index[:3]:", list(df.index[:3]))
    print("index.name:", df.index.name)

    # 单次条数上限探测
    sep("2) count 上限探测（count=2000 默认 / 3000 / 8000）")
    for c in (2000, 3000, 8000):
        try:
            d = client.get_transactions(MKT, SYMBOL, count=c)
            print(f"count={c:>5} -> rows={0 if d is None else len(d)}")
        except Exception as e:  # noqa: BLE001
            print(f"count={c:>5} -> EXC {type(e).__name__}: {e}")

    # start 分页语义
    sep("3) start 分页语义（count=10, start=0/10/20）")
    for s in (0, 10, 20):
        try:
            d = client.get_transactions(MKT, SYMBOL, count=10, start=s)
            first = None
            if d is not None and len(d):
                first = d.iloc[0].to_dict()
            print(f"start={s:>3} -> rows={0 if d is None else len(d)} first={first}")
        except Exception as e:  # noqa: BLE001
            print(f"start={s:>3} -> EXC {type(e).__name__}: {e}")

    # 历史日期语义
    sep("4) date 参数语义（20260915 / 20260914 / 20260912）")
    for dt in (20260915, 20260914, 20260912):
        try:
            d = client.get_transactions(MKT, SYMBOL, count=10, date=dt)
            rows = 0 if d is None else len(d)
            sample = None
            if rows:
                sample = d.iloc[0].to_dict()
            print(f"date={dt} -> rows={rows} first={sample}")
        except Exception as e:  # noqa: BLE001
            print(f"date={dt} -> EXC {type(e).__name__}: {e}")


def probe_tick_chart(client) -> None:
    sep("5) get_tick_chart（分时）")
    for dt in (None, 20260915):
        try:
            df = client.get_tick_chart(MKT, SYMBOL, date=dt)
            print(f"date={dt} -> shape={None if df is None else df.shape} cols={None if df is None else list(df.columns)}")
            if df is not None and len(df):
                print(df.head(2).to_string())
        except Exception as e:  # noqa: BLE001
            print(f"date={dt} -> EXC {type(e).__name__}: {e}")


def probe_auction(client) -> None:
    sep("6) get_auction（集合竞价）")
    try:
        df = client.get_auction(MKT, SYMBOL)
        print("shape:", None if df is None else df.shape, "cols:", None if df is None else list(df.columns))
        if df is not None and len(df):
            print(df.head(3).to_string())
    except Exception as e:  # noqa: BLE001
        print("EXC", type(e).__name__, e)


def main() -> None:
    sep("0) 连接 TDX")
    with MacClient() as client:
        for fn in (probe_transactions, probe_tick_chart, probe_auction):
            try:
                fn(client)
            except Exception:  # noqa: BLE001
                print(f"!! {fn.__name__} 崩：")
                traceback.print_exc()


if __name__ == "__main__":
    main()
