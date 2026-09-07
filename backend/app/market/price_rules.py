"""市场交易规则单点（涨跌停幅度）。

历史上 breadth / sentiment engine / data_quality.validator 各自手写一份
涨跌停判定（逐行相同、靠 docstring 注释约定同步）——2026-09-07 健康度审查
R1 收口为单一实现。注意 halt_risk.limit_pct(board) 是「板块键」体系
（board 字符串另服务基准指数映射），属不同抽象不强行合并。
"""
from __future__ import annotations


def limit_pct(symbol: str, name: str | None = None) -> float:
    """涨跌停幅度（百分数）：创业板/科创板 20、北交所 30、ST 5、主板 10。

    判定顺序是语义的一部分：**先板块后 ST**——创业板/科创板的 ST 股仍 ±20%
    （注册制后不降档）。返回百分数（20.0）；validator 的小数口径自行 /100。
    新股上市初期等特殊阶段未在此展开（N 前缀豁免由 breadth 的容差逻辑处理）。
    """
    sym = str(symbol or "")
    if sym.startswith(("300", "301", "688", "689")):
        return 20.0
    if sym.startswith(("43", "83", "87", "92")):
        return 30.0
    if name and "ST" in name.upper():
        return 5.0
    return 10.0
