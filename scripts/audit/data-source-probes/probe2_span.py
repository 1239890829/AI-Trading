"""TDX 逐笔成交 第二轮：时间跨度 / 单调性 / bs_flag 语义 / 单日总量。"""
from __future__ import annotations

import datetime as dt

import pandas as pd
from easy_tdx import MacClient, Market

MKT = Market.SH.value


def _norm_time(s: pd.Series) -> pd.Series:
    """time 列可能是 datetime.time 或 str，统一成 'HH:MM:SS' 字符串。"""
    if s.dtype == object and len(s):
        v0 = s.iloc[0]
        if isinstance(v0, dt.time):
            return s.map(lambda x: x.strftime("%H:%M:%S") if isinstance(x, dt.time) else str(x))
        return s.astype(str)
    return s.astype(str)


def main() -> None:
    with MacClient() as client:
        # ---- A. 单日总量与时间跨度 ----
        print("=" * 72)
        print("A) 单日逐笔总量 / 时间跨度（600519.SH）")
        print("=" * 72)
        for c in (2000, 5000, 20000, 60000):
            try:
                df = client.get_transactions(MKT, "600519", count=c)
                if df is None or not len(df):
                    print(f"count={c:>6} -> 空")
                    continue
                t = _norm_time(df["time"])
                print(
                    f"count={c:>6} -> rows={len(df):>6} "
                    f"first={t.iloc[0]} last={t.iloc[-1]} "
                    f"nunique={t.nunique()} mono_inc={t.is_monotonic_increasing}"
                )
            except Exception as e:  # noqa: BLE001
                print(f"count={c:>6} -> EXC {type(e).__name__}: {e}")

        # ---- B. 分页能否覆盖全天 ----
        print()
        print("=" * 72)
        print("B) start 分页覆盖全天？(count=2000, start=0/2000/4000/.../40000)")
        print("=" * 72)
        seen: list[str] = []
        for s in range(0, 42000, 2000):
            try:
                df = client.get_transactions(MKT, "600519", count=2000, start=s)
                if df is None or not len(df):
                    print(f"start={s:>6} -> 空（推测已到尾）")
                    break
                t = _norm_time(df["time"])
                seen.append(t.iloc[0])
                print(f"start={s:>6} -> rows={len(df):>5} first={t.iloc[0]} last={t.iloc[-1]}")
            except Exception as e:  # noqa: BLE001
                print(f"start={s:>6} -> EXC {type(e).__name__}: {e}")
                break

        # ---- C. bs_flag 语义 ----
        print()
        print("=" * 72)
        print("C) bs_flag 取值分布 + 与价格变动方向对照（判主动买卖）")
        print("=" * 72)
        df = client.get_transactions(MKT, "600519", count=400)
        if df is not None and len(df):
            df = df.copy()
            df["t"] = _norm_time(df["time"])
            print("bs_flag 分布:\n", df["bs_flag"].value_counts(dropna=False).to_string())
            print("列样例:\n", df.head(12).to_string())
            # 价格上行/下行/持平时 bs_flag 的分布
            d = df.reset_index(drop=True)
            d["dp"] = d["price"].diff()
            d["dir"] = d["dp"].apply(lambda x: "up" if x > 0 else ("down" if x < 0 else "flat"))
            print("\ndir × bs_flag 交叉:\n", pd.crosstab(d["dir"], d["bs_flag"]).to_string())

        # ---- D. 成交额自洽性（vol 单位是手？） ----
        print()
        print("=" * 72)
        print("D) vol 单位自洽性：Σ(price×vol×100) 与当日成交额对照")
        print("=" * 72)
        df = client.get_transactions(MKT, "600519", count=60000)
        if df is not None and len(df):
            amt = float((df["price"] * df["vol"] * 100).sum())
            lots = float(df["vol"].sum())
            print(f"rows={len(df)}  Σvol(手)={lots:,.0f}  Σ金额(按vol×100股)={amt/1e8:.2f} 亿")
            print(f"Σtrade_count={int(df['trade_count'].sum())}")

        # ---- E. 历史日全天量（9/15） ----
        print()
        print("=" * 72)
        print("E) 历史日 9/15 总量（date=20260915）")
        print("=" * 72)
        df = client.get_transactions(MKT, "600519", count=60000, date=20260915)
        if df is not None and len(df):
            t = _norm_time(df["time"])
            print(f"rows={len(df)} first={t.iloc[0]} last={t.iloc[-1]}")
            print(f"Σvol(手)={float(df['vol'].sum()):,.0f}  Σtrade_count={int(df['trade_count'].sum())}")
            print(df.head(3).to_string())
        else:
            print("空")

        # ---- F. 深市 / 创业板兼容 ----
        print()
        print("=" * 72)
        print("F) 深市 000001.SZ / 创业板 300750.SZ")
        print("=" * 72)
        for mk, code in ((Market.SZ.value, "000001"), (Market.SZ.value, "300750")):
            try:
                d = client.get_transactions(mk, code, count=5)
                print(f"{code} -> rows={0 if d is None else len(d)}", None if d is None or not len(d) else _norm_time(d['time']).tolist())
            except Exception as e:  # noqa: BLE001
                print(f"{code} -> EXC {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
