"""TDX 逐笔 第三轮：时间轴结构判定（升序/降序/滚动窗口/跨日）。"""
from __future__ import annotations

import datetime as dt

import pandas as pd
from easy_tdx import MacClient, Market

MKT = Market.SH.value


def tstr(s: pd.Series) -> list[str]:
    out = []
    for v in s:
        if isinstance(v, dt.time):
            out.append(v.strftime("%H:%M:%S"))
        else:
            out.append(str(v))
    return out


def show(label: str, df: pd.DataFrame | None) -> None:
    print(f"\n--- {label} ---")
    if df is None or not len(df):
        print("空")
        return
    t = tstr(df["time"])
    ts = pd.Series(t)
    print(f"rows={len(df)}  min={ts.min()}  max={ts.max()}")
    print(f"mono_inc={ts.is_monotonic_increasing}  mono_dec={ts.is_monotonic_decreasing}")
    idx = sorted({0, 1, 2, len(t) // 4, len(t) // 2, len(t) * 3 // 4, len(t) - 3, len(t) - 2, len(t) - 1})
    print("采样:", [(i, t[i]) for i in idx if 0 <= i < len(t)])


def main() -> None:
    with MacClient() as client:
        print("=" * 72)
        print("G) 不同 count 下的时间轴形态（600519.SH，当日）")
        print("=" * 72)
        for c in (10, 50, 400, 1000, 2000, 4000):
            try:
                show(f"count={c}", client.get_transactions(MKT, "600519", count=c))
            except Exception as e:  # noqa: BLE001
                print(f"count={c} EXC {type(e).__name__}: {e}")

        print()
        print("=" * 72)
        print("H) count=50 全量时间序列（看排列规律）")
        print("=" * 72)
        df = client.get_transactions(MKT, "600519", count=50)
        if df is not None and len(df):
            print(tstr(df["time"]))

        print()
        print("=" * 72)
        print("I) 时间分布直方（按小时：分钟）")
        print("=" * 72)
        df = client.get_transactions(MKT, "600519", count=6000)
        if df is not None and len(df):
            t = pd.Series(tstr(df["time"]))
            print("按小时计数:\n", t.str[:2].value_counts().sort_index().to_string())
            print("唯一时间戳数:", t.nunique(), "总行数:", len(t))

        print()
        print("=" * 72)
        print("J) 盘后/中性标记（bs_flag=2/5）样本")
        print("=" * 72)
        df = client.get_transactions(MKT, "600519", count=6000)
        if df is not None and len(df):
            odd = df[df["bs_flag"].isin([2, 5])]
            print(f"bs_flag in (2,5) 行数={len(odd)}")
            if len(odd):
                o = odd.copy()
                o["t"] = tstr(o["time"])
                print(o.head(20).to_string())

        print()
        print("=" * 72)
        print("K) 深市 000001 逐笔 bs_flag 与时间（验证 15:0x 是否盘后）")
        print("=" * 72)
        d = client.get_transactions(Market.SZ.value, "000001", count=30)
        if d is not None and len(d):
            dd = d.copy()
            dd["t"] = tstr(dd["time"])
            print(dd.to_string())


if __name__ == "__main__":
    main()
