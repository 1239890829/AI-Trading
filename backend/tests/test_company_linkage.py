"""Bounded event/application/customer counterexamples; fixtures are not gold."""
from copy import deepcopy
import json

import pytest

from app.events.company_linkage import compile_analysis, parse_selection_batch, select_packet


def case(quote="本公司主要产品为精密齿轮，产品用于人形机器人关节。", *, title="全球人形机器人销量增长",
         summary="报告称人形机器人销量增长，采购和技术路线仍有不确定。", kind="product", short="合成精密"):
    return {"id": "company:600000", "symbol": "600000", "company_name": "合成精密股份有限公司",
            "short_name": short, "event": {"event_id": 10, "version_id": 20, "observation_id": 30,
            "title": title, "summary": summary}, "documents": [{"id": "h1", "excerpts": [
            {"kind": kind, "page": 18, "quote": quote}]}]}


def selected(*, channel="application_demand", phrase="精密齿轮", anchor="人形机器人", bridge="h1:0",
             reason="supported", status="linked", event_phrase="人形机器人销量增长"):
    return {"id": "company:600000", "event_id": 10, "status": status,
            "links": [{"fact_id": "h1:0", "phrase": phrase, "anchor": anchor,
                       "channel": channel, "bridge_fact_id": bridge}] if status == "linked" else [],
            "assessment": {"fact_id": "h1:0", "company_phrase": phrase,
                           "event_phrase": event_phrase, "reason_code": reason},
            "missing_codes": ["customer", "order", "exposure", "timing"]}


def test_explicit_product_application_supports_conditional_exposure_not_orders():
    analysis, rejected = compile_analysis(case(), selected())
    assert analysis["status"] == "conditional" and not rejected
    assert "精密齿轮" in analysis["summary"] and "人形机器人" in analysis["summary"]
    assert "验证、定点、采购和交付" in analysis["chain"][1]["text"]
    assert "未证明本公司参与该事件或已获订单" in analysis["chain"][1]["text"]
    assert any("客户认证" in m for m in analysis["missing"])


@pytest.mark.parametrize("quote", [
    "本公司提供咨询服务；客户主要产品为精密齿轮，产品用于人形机器人关节。",
    "本公司主要产品为精密齿轮。行业中的其他产品用于人形机器人关节。",
    "本公司主要产品为精密齿轮，不用于人形机器人关节。",
    "本公司不生产精密齿轮，行业产品用于人形机器人关节。",
    "公司生产精密齿轮，主要用于乘用车，尚未应用于人形机器人。",
])
def test_other_party_unbound_or_negated_application_cannot_create_exposure(quote):
    analysis, rejected = compile_analysis(case(quote), selected())
    assert analysis["status"] == "insufficient" and rejected


def test_registration_scope_is_not_downstream_procurement():
    source = case(title="某外部企业新设机器人公司", summary="公司成立，工商经营范围包括人形机器人研发。")
    analysis, rejected = compile_analysis(source, selected(event_phrase="经营范围包括人形机器人研发"))
    assert analysis["status"] == "insufficient" and rejected
    assert "采购或需求变化" in rejected[0]


def test_abstention_explains_company_and_event_difference_with_both_references():
    source = case("本公司主要产品为数字文化展览系统。", title="港口发布自动化调度方案",
                  summary="港口推出自动化调度方案，未说明本公司的供应关系。")
    choice = selected(phrase="数字文化展览系统", event_phrase="港口发布自动化调度方案",
                      status="no_supported_link", reason="unmapped_application")
    analysis, rejected = compile_analysis(source, choice)
    assert not rejected and analysis["status"] == "no_supported_link"
    assert "数字文化展览系统" in analysis["summary"] and "港口发布" in analysis["summary"]
    assert "规格或场景" in analysis["summary"]
    assert set(analysis["chain"][0]["evidence_ids"]) == {"h1:0", "event:title"}
    assert "没有业务" not in analysis["summary"]


def test_named_customer_activity_does_not_bind_new_platform_or_product_order():
    source = case("公司生产高速数据线束；公司已取得大众、奥迪的项目定点，其中部分项目已供货。",
                  title="大众新CMP平台量产", summary="大众新CMP平台开始量产。", kind="order")
    analysis, rejected = compile_analysis(source, selected(channel="customer_activity", phrase="高速数据线束",
                            anchor="大众", event_phrase="大众新CMP平台量产"))
    assert not rejected and analysis["status"] == "conditional"
    assert "不能把整体客户关系绑定到本次平台" in analysis["chain"][1]["text"]
    assert any("集团与子公司" in s for s in analysis["counterarguments"])
    assert "已配套CMP" not in str(analysis)


@pytest.mark.parametrize("quote", [
    "公司生产高速数据线束；客户尚未披露。",
    "公司生产高速数据线束；公司没有向大众供货，也没有大众客户。",
    "公司生产高速数据线束；供应商为大众提供其他产品。",
    "公司生产高速数据线束；公司向大众采购控制器，大众为本公司供应商。",
    "公司生产高速数据线束；大众不是本公司的客户。",
    "公司生产高速数据线束；公司客户包括大众子公司。",
])
def test_anonymous_denied_supplier_or_partial_name_cannot_gain_customer_bridge(quote):
    source = case(quote, title="大众新平台量产", summary="大众新平台量产。", kind="order")
    analysis, rejected = compile_analysis(source, selected(channel="customer_activity", phrase="高速数据线束",
                                  anchor="大众", event_phrase="大众新平台量产"))
    assert analysis["status"] == "insufficient" and rejected


def test_group_prefix_does_not_link_two_different_subsidiaries():
    source = case("公司生产高速数据线束；公司客户包括上汽大众。", title="上汽通用五菱发布新车",
                  summary="上汽通用五菱发布新车并采购高速数据线束。", kind="order")
    analysis, rejected = compile_analysis(source, selected(channel="customer_activity", phrase="高速数据线束",
                            anchor="上汽", event_phrase="上汽通用五菱发布新车"))
    assert analysis["status"] == "insufficient" and rejected


def test_numeric_source_product_name_is_not_an_invented_financial_number():
    source = case("本公司主要产品为5G通信模组。", title="5G通信模组采购增加", summary="5G通信模组采购需求增加。")
    analysis, rejected = compile_analysis(source, selected(channel="demand", phrase="5G通信模组",
                            anchor="5G通信模组", bridge=None, event_phrase="5G通信模组采购增加"))
    assert not rejected and analysis["status"] == "conditional"
    assert "利润增长5" not in str(analysis)


@pytest.mark.parametrize("phrase", ["20亿元", "2026年10月", "50%", "人工智能", "5G"])
def test_amount_date_percent_and_broad_terms_are_not_products(phrase):
    source = case(f"本公司主要产品为{phrase}。", title=f"{phrase}采购增加", summary=f"{phrase}采购需求增加。")
    choice = selected(channel="demand", phrase=phrase, anchor=phrase,
                      bridge=None, event_phrase=f"{phrase}采购增加")
    choice["assessment"]["company_phrase"] = source["documents"][0]["excerpts"][0]["quote"]
    analysis, rejected = compile_analysis(source, choice)
    assert analysis["status"] == "insufficient" and rejected


def test_three_character_issuer_short_name_can_bind_its_own_project():
    source = case("本公司生产数字展览系统。", title="风语筑拟建数字展览系统基地",
                  summary="风语筑拟投资建设数字展览系统基地，尚未开工。", short="风语筑")
    analysis, rejected = compile_analysis(source, selected(channel="capacity_plan", phrase="数字展览系统",
                            anchor="数字展览系统", bridge=None, event_phrase="风语筑拟建数字展览系统基地"))
    assert not rejected and analysis["status"] == "conditional"


def test_nonfirst_event_choice_keeps_exact_context_without_mutating_input():
    source = case();source["event_candidates"] = [source["event"], {**source["event"], "event_id": 11,
                              "version_id": 21, "observation_id": 31, "title": "另一条人形机器人销量增长"}]
    choice = selected();choice["event_id"] = 11;before = deepcopy((source, choice));packet = select_packet(source, choice)
    assert packet["event"]["event_id"] == 11 and packet["event"]["version_id"] == 21
    assert "event_candidates" not in packet and packet["id"] == source["id"]
    assert (source, choice) == before
    assert parse_selection_batch(json.dumps([choice]), [source]) == {source["id"]: choice}
    choice["event_id"] = 99
    with pytest.raises(ValueError):select_packet(source, choice)


@pytest.mark.parametrize("patch", [{"fact_id": "foreign:0"}, {"company_phrase": "来源没有的产品"},
                                  {"event_phrase": "来源没有的订单"}, {"reason_code": "model_decides_true"}])
def test_assessment_cannot_invent_a_source_or_free_factual_explanation(patch):
    choice = selected();choice["assessment"].update(patch)
    with pytest.raises(ValueError):compile_analysis(case(), choice)


def test_model_cannot_cite_unseen_tail_of_truncated_event_summary():
    source = case();source["event"]["summary"] = "资料" * 900 + "公司秘密订单"
    choice = selected();choice["assessment"]["event_phrase"] = "公司秘密订单"
    with pytest.raises(ValueError):compile_analysis(source, choice)


def test_generic_assessment_label_cannot_replace_company_evidence():
    choice = selected();choice["assessment"]["company_phrase"] = "主要产品"
    with pytest.raises(ValueError):compile_analysis(case(), choice)


@pytest.mark.parametrize("number", ["1600000万元", "6000000万元", "600000万元", "600000 万元", "600000　亿元", "600000美元"])
def test_industry_amount_containing_symbol_is_not_an_issuer_identity(number):
    title = f"行业规模{number}，精密齿轮订单增长"
    source = case("本公司生产精密齿轮。", title=title,
                  summary=f"行业统计显示{number}，精密齿轮订单增长，未提任何上市公司。")
    analysis, rejected = compile_analysis(source, selected(channel="order_execution", phrase="精密齿轮",
                            anchor="精密齿轮", bridge=None, event_phrase=title))
    assert analysis["status"] == "insufficient" and rejected


def test_exact_security_code_still_binds_an_issuer_event():
    title = "证券代码600000的公司签订精密齿轮订单"
    source = case("本公司生产精密齿轮。", title=title, summary=title)
    analysis, rejected = compile_analysis(source, selected(channel="order_execution", phrase="精密齿轮",
                            anchor="精密齿轮", bridge=None, event_phrase=title))
    assert analysis["status"] == "conditional" and not rejected



def test_disclosed_core_application_keeps_its_same_product_subject():
    # CNINFO H1 physical page12, quote SHA d803dd15cf80c8dfd2a01de8ee552bc72d64e9c6f7c14abe29ae28e3c2e96c17;
    # event20763/11900/12118. Author-selected compiler regression, not model output/gold.
    quote = "公司核心产品包含柔性传感器薄膜、触觉传感组件等，产品可精准模拟人体皮肤，实现压力、纹理、温度多维度感知与实时反馈，核心适配人形机器人灵巧手、数据采集设备、医疗康养等高端应用场景。"
    title = "上半年全球人形机器人销量达到3.6万台，机器人ETF鹏华(159278)标的指数涨超5%"
    summary = "近日，国际咨询机构弗若斯特沙利文发布《2026年全球人形机器人市场研究报告》。今年上半年，全球人形机器人销量达到3.6万台，市场收入达到5.4亿美元。"
    source = case(quote, title=title, summary=summary)
    analysis, rejected = compile_analysis(source, selected(phrase="柔性传感器薄膜",
                            event_phrase="上半年全球人形机器人销量达到3.6万台"))
    assert not rejected and analysis["status"] == "conditional"
    assert "本次未证明本公司参与该事件或已获订单" in analysis["chain"][1]["text"]
    assert "行业销量不能直接换算本公司订单或利润" in "".join(analysis["counterarguments"])


@pytest.mark.parametrize("other", ["另一产品可", "其他传感器核心"])
def test_implicit_application_subject_cannot_borrow_a_named_other_product(other):
    source = case(f"本公司主要产品为精密齿轮，{other}用于人形机器人。")
    analysis, rejected = compile_analysis(source, selected())
    assert analysis["status"] == "insufficient" and rejected
