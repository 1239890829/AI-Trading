"""Synthetic source counterexamples for the frozen eleven-case risk axes.

These fixtures test source/role/channel behavior. They are not public disclosures,
human gold, stock recommendations, or a semantic accuracy benchmark.
"""
from copy import deepcopy

import pytest

from app.events.company_reasoning import compile_analysis


def packet(title, summary, quote, *, kind="product"):
    return {
        "id": "synthetic-mechanism", "symbol": "600000",
        "company_name": "合成精密股份有限公司", "short_name": "合成精密",
        "captured_at": "2026-10-20T10:00:00+08:00",
        "event": {"event_id": 10, "version_id": 20, "observation_id": 30,
                  "title": title, "summary": summary,
                  "available_at": "2026-10-20T09:30:00+08:00"},
        "documents": [{"id": "h1", "excerpts": [
            {"kind": kind, "page": 18, "quote": quote}]}],
    }


def selection(*, phrase="精密连接器", anchor="精密连接器", channel="demand", fact_id="h1:0"):
    return {"id": "synthetic-mechanism", "status": "linked", "links": [
        {"fact_id": fact_id, "phrase": phrase, "anchor": anchor, "channel": channel}],
        "missing_codes": ["customer", "order", "exposure"]}


def assert_no_chain(source, selected):
    analysis, rejected = compile_analysis(source, selected)
    assert analysis["status"] in {"insufficient", "no_supported_link"}
    assert analysis["chain"] == []
    assert rejected, "a rejected linked selection must retain its concrete reason"
    return analysis


@pytest.mark.parametrize("quote", [
    "本公司主要产品包括精密连接器，向设备制造企业销售。",
    "本公司为北美客户生产精密连接器，产品用于通信设备。",
    "本公司生产精密连接器，客户使用这些部件生产汽车线束。",
    "本公司生产精密连接器，不生产机器人整机。",
])
def test_actual_issuer_production_is_useful_even_with_customer_or_other_product_scope(quote):
    source = packet("精密连接器采购需求增加", "相关行业对精密连接器的采购需求增长。", quote)
    analysis, rejected = compile_analysis(source, selection())
    assert analysis["status"] == "conditional"
    assert len(analysis["chain"]) == 1
    assert rejected == []
    assert set(analysis["chain"][0]["evidence_ids"]) == {"event:summary", "h1:0"}
    assert "若事件实际影响" in analysis["summary"]


@pytest.mark.parametrize("quote", [
    "本公司提供咨询服务，客户生产精密连接器。",
    "本公司提供咨询服务；本公司的客户在其境外独立工厂负责开发并批量生产精密连接器。",
    "本公司主营管理软件；客户采购精密连接器用于通信设备装配。",
])
def test_customer_production_or_purchase_does_not_become_issuer_production(quote):
    source = packet("精密连接器需求增长", "精密连接器采购订单增加。", quote, kind="business")
    assert_no_chain(source, selection())


def test_general_customer_list_cannot_bind_a_product_or_the_event():
    source = packet("甲公司扩大精密连接器采购", "甲公司精密连接器采购需求增加。",
                    "本公司主要客户包括甲公司、乙公司，主要产品另有精密连接器。", kind="customer")
    assert_no_chain(source, selection())


def test_anonymous_enterprise_orders_cannot_be_claimed_by_issuer_execution():
    source = packet("江西某企业精密连接器订单排至明年", "该匿名企业披露精密连接器订单，未提供企业证券身份。",
                    "本公司主要产品为精密连接器。")
    assert_no_chain(source, selection(channel="order_execution"))


@pytest.mark.parametrize("phrase", ["人工智能", "AI技术", "相关产品"])
def test_broad_technology_or_product_words_are_not_a_specific_operating_link(phrase):
    source = packet(f"{phrase}采购需求增长", f"业内称{phrase}需求增加。", f"本公司提供{phrase}。")
    assert_no_chain(source, selection(phrase=phrase, anchor=phrase))


@pytest.mark.parametrize("summary", [
    "精密连接器概念股涨停，龙虎榜显示成交额和换手率。",
    "精密连接器概念股涨停，龙虎榜显示成交额；公司称不涉及新增订单。",
])
def test_market_only_or_explicit_no_new_order_does_not_gain_an_operating_chain(summary):
    source = packet("精密连接器概念股龙虎榜", summary, "本公司主要产品包括精密连接器。")
    assert_no_chain(source, selection())


def test_actual_purchase_supports_cost_channel_without_claiming_issuer_is_the_producer():
    source = packet("覆铜板原材料价格上涨", "覆铜板采购价格上涨，成本变化待观察。",
                    "本公司采购覆铜板作为印制线路板的原材料。", kind="business")
    analysis, rejected = compile_analysis(source, selection(phrase="覆铜板", anchor="覆铜板", channel="input_cost"))
    assert analysis["status"] == "conditional" and rejected == []
    assert "采购成本及成本转嫁" in analysis["summary"]


def test_customer_procurement_is_not_issuer_input_cost_exposure():
    source = packet("覆铜板原料价格上涨", "覆铜板采购价格上涨。",
                    "本公司的客户采购覆铜板作为原料；本公司主要销售咨询软件。", kind="business")
    assert_no_chain(source, selection(phrase="覆铜板", anchor="覆铜板", channel="input_cost"))


def test_capex_plan_retains_stage_and_does_not_generate_amount_or_realized_output():
    source = packet("合成精密拟建显示面板产线", "合成精密拟投资20亿元建设显示面板产线，尚未开工。",
                    "本公司主要产品为OLED显示面板。")
    analysis, rejected = compile_analysis(source, selection(phrase="显示面板", anchor="显示面板", channel="capacity_plan"))
    assert analysis["status"] == "conditional" and rejected == []
    assert "20亿" not in analysis["summary"]
    assert "建设计划不等于已投产" in "；".join(analysis["counterarguments"])
    assert "计划日期不当实际收入日期" in "；".join(analysis["counterarguments"])


def test_two_bases_cannot_transfer_completed_status_to_the_unbuilt_project():
    source = packet("合成精密披露两个火箭贮箱基地进展", "合成精密东台火箭贮箱基地已投产；广州火箭贮箱基地尚未开工。",
                    "本公司生产火箭贮箱及相关结构件。")
    analysis, rejected = compile_analysis(source, selection(phrase="火箭贮箱", anchor="火箭贮箱", channel="capacity_plan"))
    assert analysis["status"] == "conditional" and rejected == []
    assert "广州已投产" not in analysis["summary"]
    assert "不同基地、子项目金额和时间不合并" in "；".join(analysis["counterarguments"])


def test_issuer_mention_elsewhere_does_not_transfer_an_external_factory_plan():
    source = packet("合成精密回应外部企业建设精密连接器工厂", "甲公司拟建精密连接器工厂；合成精密表示仅开展市场研究，不参与上述建设。",
                    "本公司生产精密连接器。")
    assert_no_chain(source, selection(channel="capacity_plan"))


def test_signed_contract_is_not_delivery_revenue_or_a_named_customer():
    source = packet("合成精密签订算力组网合同", "合成精密与W公司签订算力组网服务合同，含税总额3.15亿元，交付验收尚待后续履行。",
                    "本公司提供算力组网配套服务。", kind="business")
    analysis, rejected = compile_analysis(source, selection(phrase="算力组网配套服务", anchor="算力组网", channel="order_execution"))
    assert analysis["status"] == "conditional" and rejected == []
    assert "3.15" not in analysis["summary"]
    assert "不等于确认收入" in "；".join(analysis["counterarguments"])
    assert "不认定已获单、增收或盈利" in analysis["chain"][0]["text"]


def test_subsidiary_joint_contract_does_not_get_allocation_or_revenue_invented():
    source = packet("合成精密下属乙公司联合签署精密连接器合同", "合成精密下属乙公司与丙公司联合签署精密连接器合同，金额与分工未提供。",
                    "本公司下属乙公司主要产品为精密连接器。", kind="business")
    analysis, rejected = compile_analysis(source, selection(channel="order_execution"))
    assert analysis["status"] == "conditional" and rejected == []
    assert "联合签约金额不全归本公司" in "；".join(analysis["counterarguments"])


def test_procurement_regulatory_event_does_not_invent_direct_chipmaker_partnership():
    source = packet("合成精密图形处理器采购披露收到监管函", "合成精密与X公司签署图形处理器采购协议，因相关披露收到监管函。",
                    "公司采购图形处理器用于研发测试。", kind="business")
    analysis, rejected = compile_analysis(source, selection(phrase="图形处理器", anchor="图形处理器", channel="business_risk"))
    assert analysis["status"] == "conditional" and rejected == []
    assert "直接合作" not in analysis["summary"]
    assert "合规" in analysis["summary"]


@pytest.mark.parametrize("patch", [
    {"fact_id": "foreign:0"}, {"phrase": "来源没有的连接器"}, {"anchor": "不存在的产品"},
    {"phrase": "精密连接器订单已经全部交付"},
])
def test_foreign_fact_or_changed_phrase_anchor_cannot_pass_through(patch):
    source = packet("精密连接器需求增长", "精密连接器采购订单增加。", "本公司生产精密连接器。")
    selected = selection()
    selected["links"][0].update(patch)
    assert_no_chain(source, selected)


def test_event_reference_points_to_the_field_that_actually_supplied_the_anchor():
    source = packet("精密连接器采购需求增长", "该公司尚待补充其他资料。", "本公司生产精密连接器。")
    analysis, rejected = compile_analysis(source, selection())
    assert analysis["status"] == "conditional" and rejected == []
    references = analysis["chain"][0]["evidence_ids"]
    assert "event:title" in references
    assert "event:summary" not in references


@pytest.mark.parametrize("extra", [
    {"summary": "公司已新增收入20亿元"}, {"phase": "广州已投产"}, {"customer": "特定未具名客户的实名"},
])
def test_model_cannot_add_free_factual_number_project_stage_or_customer_name(extra):
    source = packet("精密连接器需求增长", "精密连接器采购需求增加。", "本公司生产精密连接器。")
    selected = selection()
    selected.update(extra)
    with pytest.raises(ValueError):
        compile_analysis(source, selected)


def test_selection_and_source_records_remain_unchanged_for_audit():
    source = packet("精密连接器需求增长", "精密连接器采购需求增加。", "本公司生产精密连接器。")
    selected = selection()
    original = deepcopy((source, selected))
    compile_analysis(source, selected)
    assert (source, selected) == original


@pytest.mark.parametrize("wrapper", [lambda s:s,lambda s:"```json\n"+s+"\n```",lambda s:"```\n"+s+"\n```"])
def test_bounded_batch_accepts_only_complete_exact_cases(wrapper):
    import json
    from app.events.company_reasoning import parse_selection_batch
    source=packet("精密连接器需求", "精密连接器采购需求增长。", "本公司生产精密连接器。")
    assert parse_selection_batch(wrapper(json.dumps([selection()])),[source]) == {source["id"]:selection()}


@pytest.mark.parametrize("value", [[], [selection(),selection()], [{"id":[]}], {"id":"synthetic-mechanism"},
                                    [{**selection(),"id":"wrong"}]])
def test_batch_rejects_missing_duplicate_foreign_or_invalid_identity(value):
    import json
    from app.events.company_reasoning import parse_selection_batch
    source=packet("精密连接器需求", "精密连接器采购需求增长。", "本公司生产精密连接器。")
    with pytest.raises(ValueError):parse_selection_batch(json.dumps(value),[source])


def test_batch_rejects_oversized_and_trailing_explanations():
    import json
    from app.events.company_reasoning import parse_selection_batch
    source=packet("精密连接器需求", "精密连接器采购需求增长。", "本公司生产精密连接器。")
    for text in (" "*20001,json.dumps([selection()])+"其他说明"):
        with pytest.raises(ValueError):parse_selection_batch(text,[source])


def test_company_supply_has_its_own_channel_without_inventing_demand_or_end_product():
    source=packet("合成精密为设备提供精密连接器", "合成精密为客户的地面设备提供精密连接器。", "本公司生产精密连接器。")
    analysis,rejected=compile_analysis(source,selection(channel="product_supply"))
    assert analysis["status"]=="conditional" and rejected==[]
    assert "部件供货" in analysis["summary"]
    assert "不扩大为整机、整车或星载" in "；".join(analysis["counterarguments"])
    source["event"]["title"]="甲公司为设备提供精密连接器"
    source["event"]["summary"]="甲公司为客户提供精密连接器。"
    assert_no_chain(source,selection(channel="product_supply"))


def test_company_financial_channel_binds_issuer_metrics_instead_of_matching_metric_to_product():
    source=packet("合成精密预计资本开支降低", "合成精密披露资本开支将减少、折旧分阶段变化。", "本公司主要产品包括精密连接器。")
    selected=selection(anchor="资本开支",channel="capital_intensity")
    analysis,rejected=compile_analysis(source,selected)
    assert analysis["status"]=="conditional" and rejected==[]
    assert "投资现金流" in analysis["summary"]
    assert "不自动等于盈利增长" in "；".join(analysis["counterarguments"])
    source["event"]["title"]="甲公司资本开支降低"
    source["event"]["summary"]="甲公司资本开支减少，合成精密只提供咨询。"
    assert_no_chain(source,selected)


def test_financial_exception_cannot_transfer_arbitrary_subject_or_customer_role():
    source=packet("合成精密资本开支变化", "合成精密资本开支下降。", "本公司提供咨询，客户生产精密连接器。")
    assert_no_chain(source,selection(anchor="资本开支",channel="capital_intensity"))
    source["documents"][0]["excerpts"][0]["quote"]="本公司生产精密连接器。"
    assert_no_chain(source,selection(anchor="精密连接器",channel="capital_intensity"))


def test_joint_context_rejection_does_not_claim_that_named_issuer_was_absent():
    source=packet("合成精密收到监管函", "合成精密与X公司签署图形处理器采购合同。", "公司采购图形处理器用于研发。",kind="order")
    result=assert_no_chain(source,selection(phrase="图形处理器",anchor="图形处理器",channel="business_risk"))
    assert "未确认" in "；".join(result["missing"])
    assert "事件未明确本公司主体" not in "；".join(result["missing"])
