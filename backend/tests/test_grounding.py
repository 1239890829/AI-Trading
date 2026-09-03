"""接地校验（grounding gate）纯函数测试。

覆盖 `app/core/grounding.py` 三类拒绝码中的两类可判定检测：
- OUT_OF_SCOPE_INFERENCE：指令性交易建议词形（规则判读自己的措辞必须放行）
- EVIDENCE_NOT_FOUND：数字接地（同值/符号/单位换算宽容，编造即拒）
- SCHEMA_VIOLATION 不在此测——格式越权由各消费点白名单负责
  （news `_valid_patch`、review 维度 key 过滤），见 tests/test_llm.py。

消费点接入测试在 tests/test_llm.py「接地校验接入」段。
"""
import sys

sys.path.insert(0, ".")

from app.core.grounding import (
    CODE_EVIDENCE_NOT_FOUND,
    CODE_OUT_OF_SCOPE_INFERENCE,
    advice_violations,
    evidence_pool,
    grounding_violations,
    number_violations,
)

# ---------------------------------------------------------------- 指令性建议


def test_advice_blocks_directive_forms():
    assert advice_violations("建议逢低买入，把握分歧转一致的节奏")
    assert advice_violations("该股可以重仓")
    assert advice_violations("止损位 10.32")
    assert advice_violations("目标价 12.5 元")
    assert advice_violations("建议仓位控制在 20%")
    assert advice_violations("果断清仓离场")


def test_advice_spares_rule_engine_wording():
    """规则判读自己的措辞必须放行——宁可漏报不可误杀。"""
    assert advice_violations("不宜追高，等待缩量企稳") == []
    assert advice_violations("建议控制仓位、减少出手频率") == []
    assert advice_violations("可以关注，暂不参与") == []
    assert advice_violations("情绪分歧期应降低仓位或空仓") == []
    assert advice_violations("回避跟风股") == []


# ---------------------------------------------------------------- 数字接地


def test_number_accepts_same_value_and_unit_conversion():
    ev = ["涨停 55 家，跌停 10 家", "上证 3000.00 (-0.50%)", "成交 1,297.40 亿"]
    # 同值直引
    assert number_violations("涨停 55 家", ev) == []
    # 千分位金额同值
    assert number_violations("全天成交 1,297.40 亿", ev) == []
    # 百分数与底稿 13.20% 归一化同值
    assert number_violations("涨幅 13.2%", ["涨幅 13.20%"]) == []
    # 符号不敏感：底稿 -0.50% ↔ 复述"下跌 0.50%"
    assert number_violations("上证下跌 0.50%", ev) == []
    # %↔bp 刚性换算互认
    assert number_violations("滑点约 50bp", ["滑点 0.50%"]) == []
    assert number_violations("滑点 0.5%", ["滑点 50bp"]) == []


def test_number_rejects_fabricated_values():
    ev = ["涨停 55 家，跌停 10 家", "上证 3000.00 (-0.50%)"]
    assert number_violations("涨停 88 家，情绪过热", ev) == ["88 家"]
    # 无证据池 = 全部拒绝（news 摘要缺原文时的兜底语义）
    assert number_violations("增长 35.7%", []) == ["35.7%"]


def test_number_skips_integers_and_free_conversions():
    # 纯整数（序数/列举）不校验：4/5 比分、日期、条数
    assert number_violations("5 条主线里 4 条走强", []) == []
    assert number_violations("2026 年 9 月 1 日复盘", []) == []
    # 亿/万单位本身不在 token 内，只校验数值同值
    assert number_violations("成交 1,297.4 亿", ["成交 1,297.40 亿"]) == []
    # 数值都不同（1.3 万亿=13000 亿 ≠ 1297.40 亿）→ 仍然拒绝
    assert number_violations("成交约 1.3 万亿", ["成交 1,297.40 亿"]) != []


# ---------------------------------------------------------------- 合并与证据池


def test_grounding_violations_returns_structured_codes():
    v = grounding_violations("建议买入，涨停 88 家", ["涨停 55 家"])
    codes = {x["code"] for x in v}
    assert codes == {CODE_OUT_OF_SCOPE_INFERENCE, CODE_EVIDENCE_NOT_FOUND}
    assert all("detail" in x for x in v)
    # 干净文本 = 空列表
    assert grounding_violations("情绪分歧加剧", ["情绪分歧加剧"]) == []


def test_evidence_pool_serializes_dict_extra():
    pool = evidence_pool("涨停 55 家", None, extra={"limit_up": 55})
    assert pool[0] == "涨停 55 家"
    assert '"limit_up": 55' in pool[1]
    # extra 里的数字也算有据
    assert number_violations("涨停 55 家", pool) == []
