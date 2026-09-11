"""P1-2 慢形态验证（一次性研究脚本，2026-09-09）

目标：验证「试盘-缩量回踩不破位」（KB-STOCK-23 规律 a / KB-STOCK-24 潜伏链②③）
作为选股前置信号是否有样本外区分度——即形态确认后 N 日内涨停/大涨概率是否
显著高于全市场基线。**有区分度才进 tech_score（SCORER v4）；否则只留知识不接线。**

口径与纪律：
- 数据：marketdb daily_k（原始 OHLCV，非复权——形态是短窗口量价结构，除权
  影响小；换手缺流通股本列，此版不含"换手<2%"硬阈值，用缩量横盘代理）
- 形态定义（首版参数来自百大案例语义，**验证用不调优**，防数据窥探）：
  试盘日 T：涨幅∈[2%, 涨停线-0.5%) 且 量 > 前5日均量×1.3 且 上影/实体>0.4（冲高回落）
  确认日 C（T+1..T+6 首个）：close<close_T 且 量≤volume_T×0.7 且
    期间最低价 > T 前 5 日最低收盘（不破位）
  标签：C 后 1..5 根内出现涨停（≥涨停线-0.3%）为命中
- 基线：同一时间窗内全市场随机交易日后 5 日涨停率（抽样 1000 只降噪）
- 涨停线按代码前缀：30/68→20%，8/4/9→30%，其余→10%（ST 无名称信息，近似 10%）
"""
from __future__ import annotations

import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import duckdb

DB = Path(__file__).resolve().parents[1] / "data" / "marketdb" / "market.duckdb"
LOOKBACK_DAYS = 210        # ~150 交易日
MAX_AFTER_DAYS = 6         # 试盘后找确认日的窗口
HIT_WINDOW = 20            # 确认日后观察天数（慢信号需长窗）


def limit_pct(code: str) -> float:
    """涨跌停幅度（**小数**，0.20/0.30/0.10）。

    2026-09-11 收口（S1-6）：本脚本此前是**第三份手写实现**（`startswith(("30","68"))`
    / `("8","4","92")`），与 `app/market/price_rules.limit_pct` 的段清单已经不一致
    （脚本含 8/4 全段、缺 302，唯一判定点则相反）——研究脚本与生产口径漂移会让
    核验结论不可复现。现改为委托唯一判定点，仅做「百分数 → 小数」换算。
    """
    from app.market.price_rules import limit_pct as _canonical
    return _canonical(code.split(".")[0]) / 100.0


def main() -> None:
    con = duckdb.connect(str(DB), read_only=True)
    mx = con.execute("select max(date_ms) from daily_k").fetchone()[0]
    lo = mx - LOOKBACK_DAYS * 86_400_000
    rows = con.execute(
        "select thscode, date_ms, open_price, high_price, low_price, close_price, volume "
        "from daily_k where date_ms >= ? order by thscode, date_ms",
        [lo],
    ).fetchall()
    con.close()
    print(f"窗口: {datetime.fromtimestamp(lo/1000):%Y-%m-%d} ~ "
          f"{datetime.fromtimestamp(mx/1000):%Y-%m-%d}  行数 {len(rows)}")

    by_code: dict[str, list] = defaultdict(list)
    for code, t, o, h, l, c, v in rows:
        if o is None or h is None or l is None or c is None or v is None:
            continue
        by_code[code].append((t, o, h, l, c, v))
    codes = list(by_code)
    print(f"股票数 {len(codes)}")

    lp = limit_pct
    hits: dict[int, int] = defaultdict(int)   # 确认日 +1..+N 天 涨停命中计数
    triggers = 0
    base_days = 0
    base_hits = 0
    base_days20 = 0
    base_hits20 = 0
    for ci, code in enumerate(codes):
        bars = by_code[code]
        cap = lp(code)
        n = len(bars)
        th_up = cap - 0.004
        # 索引对齐：i 是自然交易日（停牌日缺失不影响——prev 用前一根）
        for i in range(n):
            # 基线抽样：20% 股票记「任意日 后5 日涨停率」
            if ci % 5 == 0 and i + 1 < n:
                base_days += 1
                base_days20 += 1
                for k in range(1, 6):
                    j = i + k
                    if j >= n:
                        break
                    pc = bars[j - 1][4]
                    if pc > 0 and bars[j][4] / pc - 1 >= th_up:
                        base_hits += 1
                        break
                for k in range(1, HIT_WINDOW + 1):
                    j = i + k
                    if j >= n:
                        break
                    pc = bars[j - 1][4]
                    if pc > 0 and bars[j][4] / pc - 1 >= th_up:
                        base_hits20 += 1
                        break
            if i < 25 or i + MAX_AFTER_DAYS >= n:
                continue
            # --- 潜伏前提（KB-STOCK-24：抛压真空后才启动）---
            # 试盘前 20 根：横盘（close 振幅 ≤30%）+ 近期量不高于更早（缩量状态）
            w_close = [bars[k][4] for k in range(i - 20, i)]
            amp = (max(w_close) - min(w_close)) / min(w_close)
            if amp > 0.30:
                continue
            vol_recent = sum(bars[k][5] for k in range(i - 10, i)) / 10
            vol_prior = sum(bars[k][5] for k in range(i - 40, i - 10)) / 30
            if vol_prior <= 0 or vol_recent > vol_prior * 1.2:
                continue  # 未处于相对缩量 → 非潜伏后试盘
            # --- 形态 A 判定 ---
            o, h, l, c, v = bars[i][1:]
            pc = bars[i - 1][4]
            if pc <= 0:
                continue
            chg = c / pc - 1
            if not (0.02 <= chg < cap - 0.01):      # 未涨停放量试盘
                continue
            v5 = sum(bars[k][5] for k in range(max(0, i - 5), i)) / 5
            if v5 <= 0 or v < v5 * 1.3:             # 放量
                continue
            body = max(o, c) - min(o, c)
            upper = h - max(o, c)
            if body > 0 and upper / body < 0.4:     # 冲高回落（上影显著）
                continue
            # 平台低点（试盘前 5 日最低收盘，防破位基准）
            floor = min(bars[k][4] for k in range(max(0, i - 5), i))
            # 找确认日 C
            for k in range(1, MAX_AFTER_DAYS + 1):
                j = i + k
                if j >= n or j + HIT_WINDOW >= n:
                    break
                cj = bars[j]
                if cj[4] >= c:                       # 未回落（继续涨）→ 非本形态
                    continue
                if cj[5] > v * 0.7:                  # 未缩量
                    continue
                # 期间（T..C）最低价不破平台
                seg_low = min(bars[x][2] for x in range(i, j + 1))
                if seg_low < floor:
                    break                            # 破位 → 终止该试盘
                triggers += 1
                for kk in range(1, HIT_WINDOW + 1):
                    m = j + kk
                    if m >= n:
                        break
                    pmc = bars[m - 1][4]
                    if pmc > 0 and bars[m][4] / pmc - 1 >= th_up:
                        hits[kk] += 1
                break                                # 每试盘只计首个确认
        if ci % 1000 == 0:
            print(f"  进度 {ci}/{len(codes)} 触发 {triggers}", flush=True)

    def pct(c): return c / max(triggers, 1) * 100
    hit5 = sum(hits[k] for k in range(1, 6))
    hit10 = sum(hits[k] for k in range(1, 11))
    hit20 = sum(hits[k] for k in range(1, HIT_WINDOW + 1))
    print("\n=== 结果（口径 2：潜伏前提 + 试盘回踩）===")
    print(f"触发次数: {triggers}")
    print(f"  确认后 5 日内涨停 : {hit5:4d}（{pct(hit5):.1f}%） | 基线 {base_hits}/{base_days}（{base_hits/max(base_days,1)*100:.1f}%）")
    print(f"  确认后 10 日内涨停: {hit10:4d}（{pct(hit10):.1f}%）")
    print(f"  确认后 20 日内涨停: {hit20:4d}（{pct(hit20):.1f}%） | 基线20日 {base_hits20}/{base_days20}（{base_hits20/max(base_days20,1)*100:.1f}%）")
    r5 = pct(hit5) / (base_hits / max(base_days, 1) * 100) if base_days else 0.0
    r20 = pct(hit20) / (base_hits20 / max(base_days20, 1) * 100) if base_days20 else 0.0
    print(f"  相对基线倍率: 5日 {r5:.2f}× | 20日 {r20:.2f}×")
    if triggers >= 30 and (r5 >= 1.5 or r20 >= 1.5):
        print("判定: 有样本外区分度 → 慢信号（长窗显著）候选进 SCORER v4 设计")
    else:
        print("判定: 区分度不足 → 只留知识不接线")


if __name__ == "__main__":
    sys.exit(main())
