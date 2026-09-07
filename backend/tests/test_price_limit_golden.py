"""R4 前后端指标黄金样本对照——涨跌停幅度（后端侧）。

共享样本：tests/golden/price_limit_golden.json（前端 vitest
apps/web/lib/price-limit-golden.test.ts 消费同一文件）。
- be 字段 = 后端 price_rules.limit_pct 期望值；null 表示该样本后端不适用（跳过）；
- divergent 样本是设计内已知分歧（halt_risk ST 决策后统一），照常断言各自行为；
- 口径变更时必须更新样本并双端同批提交——本测试红 = 两端漂移或样本过期。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.market.price_rules import limit_pct

GOLDEN = Path(__file__).resolve().parent / "golden" / "price_limit_golden.json"


def _cases() -> list[dict]:
    data = json.loads(GOLDEN.read_text(encoding="utf-8"))
    cases = data.get("cases")
    assert isinstance(cases, list) and cases, "黄金样本结构异常：缺 cases 数组"
    return cases


@pytest.mark.parametrize("case", _cases(), ids=lambda c: c["symbol"] or "(empty)")
def test_price_limit_backend_matches_golden(case: dict) -> None:
    be = case.get("be")
    if be is None:
        pytest.skip(f"后端不适用（{case.get('note') or '无指数/无涨跌停概念'}）")
    assert limit_pct(case["symbol"], case.get("name")) == be, (
        f"涨跌停口径漂移：{case['symbol']}({case.get('name')}) 期望 {be}。"
        f"若是合法口径变更，更新 golden 样本并与前端同批提交"
        f"{('；分歧样本：' + case['divergent']) if case.get('divergent') else ''}"
    )


def test_golden_covers_key_segments() -> None:
    """样本覆盖面守卫：关键代码段缺失即样本本身不完整（防删样本偷懒）。"""
    syms = {c["symbol"] for c in _cases()}
    for required in ("600519", "300750", "688981", "920075", "300999", "sh000001"):
        assert required in syms, f"黄金样本缺关键段：{required}"
    divergents = [c for c in _cases() if c.get("divergent")]
    assert divergents, "分歧样本必须显式留档（删掉=隐藏口径分歧）"
