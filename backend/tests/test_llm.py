"""LLM 接入层测试（OpenAI 兼容客户端 + 两个分析器的增强与降级）。

全部走 httpx.MockTransport，不触网。覆盖三层：
1. `app/core/llm_client.py`：请求成功 / HTTP 错误 / 响应结构不对 / JSON 抽取
2. `LLMAnalyzer`：judgements 覆盖、漏答保留规则原判、未知 key 丢弃、
   解析失败上抛 → ModelRouter 显式降级
3. `LLMSummarizer`：字段校验回填、非法值保留规则原判、全部无效整体上抛、
   空输入不发请求 → SummaryRouter 显式降级
4. 接地校验接入（app/core/grounding）：指令性建议 / 无据数字被逐条丢弃
   并留痕 grounding_rejected，全被拒回退规则原判；digest 校验失败保留规则摘要
"""
from __future__ import annotations

import json as _json
import sys

import httpx
import pytest

sys.path.insert(0, ".")

from app.core.llm_client import LLMError, chat_completion, extract_json_object
from app.news.llm import LLMSummarizer
from app.news.router import SummaryRouter
from app.review.analyzers import LLMAnalyzer, RulesAnalyzer
from app.review.model_router import ModelRouter
from app.review.schemas import (
    IndexQuote,
    MarketSnapshot,
    OrderRecord,
    ReviewData,
    TradingSnapshot,
)

# ---------------------------------------------------------------- 桩


def _chat_body(content: str) -> dict:
    return {"choices": [{"message": {"content": content}}]}


def _client_with(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _json_client(payload: dict, *, calls: list | None = None) -> httpx.Client:
    """返回一个固定回复 `payload`（剥 ```json 围栏）的 chat/completions 桩客户端。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append({"url": str(request.url), "body": request.read()})
        return httpx.Response(200, json=_chat_body(
            "```json\n" + _json.dumps(payload, ensure_ascii=False) + "\n```"
        ))

    return _client_with(handler)


def _review_data() -> ReviewData:
    return ReviewData(
        trade_date="20990101",
        market=MarketSnapshot(
            trade_date="20990101",
            indices=[IndexQuote(symbol="sh000001", name="上证", close=3000.0, change_pct=-0.5)],
            sentiment={"phase": "分歧", "temperature": 30, "confidence": 0.6},
        ),
        trading=TradingSnapshot(
            trade_date="20990101",
            orders=[OrderRecord(
                id=1, symbol="600519", side="buy", price=100.0, quantity=100,
                status="filled", filled_price=100.5,
            )],
            realized_pnl=0.0, trade_count=1,
        ),
    )


def _method():
    from app.review.config import load_methodology

    return load_methodology("v1")


# ---------------------------------------------------------------- llm_client


def test_chat_completion_success_and_request_shape():
    calls: list = []
    client = _json_client({"echo": 1}, calls=calls)
    out = chat_completion(
        "https://api.example.com/v1/", "sk-test", "gpt-x",
        [{"role": "user", "content": "hi"}], client=client,
    )
    # 返回原始 content（含围栏），JSON 抽取交给 extract_json_object
    assert out.startswith("```json")
    assert calls[0]["url"] == "https://api.example.com/v1/chat/completions"  # 尾斜杠已归一
    body = _json.loads(calls[0]["body"])
    assert body["model"] == "gpt-x"
    assert body["messages"][0]["content"] == "hi"
    assert "temperature" in body


def test_chat_completion_missing_config_raises_without_request():
    with pytest.raises(LLMError, match="未配置"):
        chat_completion("", "key", "m", [])


def test_chat_completion_http_error():
    client = _client_with(lambda req: httpx.Response(500, text="boom"))
    with pytest.raises(LLMError, match="500"):
        chat_completion("https://x", "k", "m", [{"role": "user", "content": "hi"}], client=client)


def test_chat_completion_bad_body_shapes():
    cases = [
        httpx.Response(200, text="not-json"),                    # 响应不是 JSON
        httpx.Response(200, json={}),                            # 缺 choices
        httpx.Response(200, json={"choices": []}),               # 空 choices
        httpx.Response(200, json={"choices": [{"message": {}}]}),  # 缺 content
        httpx.Response(200, json=_chat_body("   ")),             # 空回复
    ]
    for resp in cases:
        client = _client_with(lambda req, r=resp: r)
        with pytest.raises(LLMError):
            chat_completion("https://x", "k", "m", [{"role": "user", "content": "hi"}], client=client)


def test_extract_json_object_variants():
    assert extract_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json_object('好的，结果如下：{"a": {"b": 2}} 请查收') == {"a": {"b": 2}}
    assert extract_json_object('{"a": 1}') == {"a": 1}
    with pytest.raises(ValueError):
        extract_json_object("没有任何 JSON")
    with pytest.raises(ValueError):
        extract_json_object('{"a": 1')  # 截断的 JSON


# ---------------------------------------------------------------- LLMAnalyzer


def test_llm_analyzer_overrides_judgements_and_marks_evidence():
    llm_judgements = {
        "trades": ["600519 滑点 50bp 属可接受范围，无需处置"],
        "unknown_key": ["这层应该被丢弃"],
    }
    client = _json_client({"judgements": llm_judgements})
    dims = LLMAnalyzer(base_url="https://x", api_key="k", model="m", client=client).analyze(
        _review_data(), _method()
    )
    assert {d.key for d in dims} == {"trades", "market", "system", "picks"}
    trades = next(d for d in dims if d.key == "trades")
    assert trades.judgements == ["600519 滑点 50bp 属可接受范围，无需处置"]
    assert trades.evidence.get("llm_enhanced") is True
    # 漏答维度保留规则原判，且不打 LLM 标记
    market = next(d for d in dims if d.key == "market")
    assert market.evidence.get("llm_enhanced") is None
    rules_market = next(
        d for d in RulesAnalyzer().analyze(_review_data(), _method())
        if d.key == "market"
    )
    assert market.judgements == rules_market.judgements


def test_llm_analyzer_bad_structure_raises():
    # judgements 不是对象
    client = _json_client({"judgements": "oops"})
    with pytest.raises(ValueError, match="judgements"):
        LLMAnalyzer(base_url="https://x", api_key="k", model="m", client=client).analyze(
            _review_data(), _method()
        )
    # 回复根本不是 JSON
    client2 = _client_with(lambda req: httpx.Response(200, json=_chat_body("抱歉我编不下去了")))
    with pytest.raises(ValueError):
        LLMAnalyzer(base_url="https://x", api_key="k", model="m", client=client2).analyze(
            _review_data(), _method()
        )


def test_llm_analyzer_http_failure_falls_back_with_degraded():
    client = _client_with(lambda req: httpx.Response(503))
    router = ModelRouter(
        requested="llm",
        llm=LLMAnalyzer(base_url="https://x", api_key="k", model="m", client=client),
    )
    dims, usage = router.analyze(_review_data(), _method())
    assert usage.actual == "rules"
    assert usage.degraded is True
    assert "llm" in usage.fallback_chain
    assert len(dims) == 4


# ---------------------------------------------------------------- LLMSummarizer


def _news_rows() -> tuple[list[dict], list[dict]]:
    news = [
        {"title": "公司收到证监会立案告知书", "summary": "因涉嫌信息披露违规被立案调查", "date": "2026-09-01"},
        {"title": "公司召开股东大会", "summary": "审议年度报告等议案", "date": "2026-09-01"},
    ]
    ann = [{"title": "贵州茅台:2026年半年度报告", "type": "半年度报告", "date": "2026-09-01"}]
    return news, ann


def test_llm_summarizer_applies_valid_fields_and_resorts():
    payload = {
        "items": [
            # 合法条目：整体覆盖
            {"id": "news:0", "importance": "高", "importance_score": 50,
             "importance_reasons": ["监管立案"], "sentiment": "偏负面",
             "sentiment_reasons": ["立案调查"], "digest": "公司因涉嫌信息披露违规被立案调查"},
            # 非法重要度/情绪被丢弃，合法 digest 仍被采用
            {"id": "news:1", "importance": "特重要", "sentiment": "超级正面",
             "digest": "公司召开股东大会审议年度议案"},
            # 合法公告条目
            {"id": "announcements:0", "importance": "中", "importance_score": 30,
             "sentiment": "中性", "digest": "贵州茅台发布2026年半年度报告"},
            # 未知 id：静默丢弃
            {"id": "news:99", "importance": "高"},
        ],
    }
    client = _json_client(payload)
    news, ann = _news_rows()
    out = LLMSummarizer(base_url="https://x", api_key="k", model="m", client=client).summarize(news, ann)

    n0, n1 = out["news"]
    assert n0["importance_score"] == 50 and n0["digest_source"] == "LLM"
    assert n0["sentiment"] == "偏负面" and n0["importance"] == "高"
    assert "numbers" in n0  # 规则层字段保留
    # 非法字段回落规则原判，digest 仍是 LLM 的
    assert n1["importance"] == "低"  # 规则层：例行事项
    assert n1["sentiment"] == "中性"
    assert n1["digest_source"] == "LLM"
    a0 = out["announcements"][0]
    assert a0["importance_score"] == 30 and a0["digest_source"] == "LLM"


def test_llm_summarizer_all_invalid_raises():
    payload = {"items": [{"id": "news:99", "importance": "高"}]}
    client = _json_client(payload)
    news, ann = _news_rows()
    with pytest.raises(ValueError, match="没有任何可应用"):
        LLMSummarizer(base_url="https://x", api_key="k", model="m", client=client).summarize(news, ann)


def test_llm_summarizer_empty_input_skips_llm():
    calls: list = []
    client = _json_client({"items": []}, calls=calls)
    out = LLMSummarizer(base_url="https://x", api_key="k", model="m", client=client).summarize([], [])
    assert out == {"news": [], "announcements": []}
    assert calls == []  # 没有条目就不该惊动 LLM


def test_llm_summarizer_failure_falls_back_with_degraded():
    client = _client_with(lambda req: httpx.Response(429))
    router = SummaryRouter(
        requested="llm",
        llm=LLMSummarizer(base_url="https://x", api_key="k", model="m", client=client),
    )
    news, ann = _news_rows()
    result, usage = router.summarize(news, ann)
    assert usage["actual"] == "rules"
    assert usage["degraded"] is True
    assert "llm" in usage["fallback_chain"]
    # 降级产物 = 完整规则输出
    assert result["news"] and result["announcements"]
    assert all(r["digest_source"] != "LLM" for r in result["news"])


# ---------------------------------------------------------------- 接地校验接入
# grounding gate：LLM 输出中的指令性建议 / 无据数字被逐条丢弃并留痕。


def test_llm_analyzer_grounding_rejects_bad_entries_and_marks_evidence():
    llm_judgements = {
        "market": [
            "情绪处于分歧阶段，宜等待方向明朗后再评估参与度",  # 合规 → 采用
            "建议逢低买入，把握分歧转一致的节奏",              # 指令性建议 → 拒
            "涨停家数 88 家，情绪已经过热",                    # 编造数字 → 拒
        ],
        "unknown_key": ["这层应该被丢弃"],
    }
    client = _json_client({"judgements": llm_judgements})
    dims = LLMAnalyzer(base_url="https://x", api_key="k", model="m", client=client).analyze(
        _review_data(), _method()
    )
    market = next(d for d in dims if d.key == "market")
    assert market.judgements == ["情绪处于分歧阶段，宜等待方向明朗后再评估参与度"]
    assert market.evidence.get("llm_enhanced") is True
    rejected = market.evidence.get("grounding_rejected") or []
    assert {v["violations"][0]["code"] for v in rejected} == {
        "OUT_OF_SCOPE_INFERENCE", "EVIDENCE_NOT_FOUND",
    }


def test_llm_analyzer_grounding_all_rejected_keeps_rules():
    client = _json_client({"judgements": {"market": ["建议立即清仓，全部离场观望"]}})
    dims = LLMAnalyzer(base_url="https://x", api_key="k", model="m", client=client).analyze(
        _review_data(), _method()
    )
    market = next(d for d in dims if d.key == "market")
    rules_market = next(
        d for d in RulesAnalyzer().analyze(_review_data(), _method()) if d.key == "market"
    )
    # 全被拒 → 规则原判，且不冒充 LLM 增强
    assert market.judgements == rules_market.judgements
    assert market.evidence.get("llm_enhanced") is None
    assert market.evidence.get("grounding_rejected") is None


def test_llm_analyzer_grounding_spares_rule_wording():
    """LLM 深化规则判定的合法措辞（不宜追高/建议控制仓位）不能被误杀。"""
    client = _json_client({"judgements": {
        "market": ["分歧加剧但不宜追高，建议控制仓位、减少出手频率"],
    }})
    dims = LLMAnalyzer(base_url="https://x", api_key="k", model="m", client=client).analyze(
        _review_data(), _method()
    )
    market = next(d for d in dims if d.key == "market")
    assert market.judgements == ["分歧加剧但不宜追高，建议控制仓位、减少出手频率"]
    assert market.evidence.get("llm_enhanced") is True
    assert market.evidence.get("grounding_rejected") is None


def test_llm_summarizer_digest_grounding_rejects():
    news = [{
        "title": "公司收到证监会立案告知书",
        "summary": "因涉嫌信息披露违规被立案调查", "date": "2026-09-01",
    }]
    payload = {"items": [{
        "id": "news:0", "importance": "高", "importance_score": 50,
        "sentiment": "偏负面",
        "digest": "建议清仓规避风险，违规减持 3% 股份",  # 建议+清仓 & 无据的 3%
    }]}
    client = _json_client(payload)
    out = LLMSummarizer(base_url="https://x", api_key="k", model="m", client=client).summarize(news, [])
    n0 = out["news"][0]
    # 合法字段照常采用；digest 被拒 → 保留规则摘要
    assert n0["importance"] == "高" and n0["importance_score"] == 50
    assert n0["digest_source"] != "LLM"
    assert n0["digest"] != "建议清仓规避风险，违规减持 3% 股份"


def test_llm_summarizer_digest_grounding_allows_source_facts():
    news = [{
        "title": "公司中标日常经营重大合同",
        "summary": "中标金额 12.5 亿元，占上年营收的 8.3%", "date": "2026-09-01",
    }]
    payload = {"items": [{
        "id": "news:0", "importance": "中", "importance_score": 30,
        "sentiment": "偏正面", "digest": "公司中标金额 12.5 亿元，占上年营收 8.3%",
    }]}
    client = _json_client(payload)
    out = LLMSummarizer(base_url="https://x", api_key="k", model="m", client=client).summarize(news, [])
    n0 = out["news"][0]
    assert n0["digest_source"] == "LLM"
    assert n0["digest"] == "公司中标金额 12.5 亿元，占上年营收 8.3%"
