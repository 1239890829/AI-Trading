from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess

import httpx
import pytest

from app.events import company_sources as source


AT = datetime(2026, 10, 20, 9, 30, tzinfo=source.BJ)
COMPANY = "示例科技股份有限公司"
SYMBOL = "000001"
BODY = (f"{COMPANY}\n证券代码：{SYMBOL}\n"
        "公司主营业务为汽车电子产品的研发、生产和销售，服务国内外汽车制造客户。"
        "主要产品包括汽车传感器和控制系统，为下游生产制造提供配套解决方案。"
        "主要客户包括汽车制造商及一级供应商，本段未按产品或型号披露具体客户。"
        "公司签订服务合同，合同最终履行金额仍取决于验收情况，不能作为固定收益承诺。"
        "报告期内部分订单交付周期延长，海外业务仍存在汇率波动及回款不确定风险。")


@pytest.fixture
def joint_stock_identity_pages():
    # De-identified layout of an official report's cover and physical page 7.
    # The legal suffix is 股份公司, not a shortened company name.
    company = "示例传动股份公司"
    profile = (f"{company} 2026年半年度报告全文\n7\n"
               "第二节 公司简介和主要财务指标\n一、公司简介\n"
               f"股票简称 示例传动 股票代码 {SYMBOL}\n"
               "股票上市证券交易所 深圳证券交易所\n"
               f"公司的中文名称 {company}\n公司的中文简称（如有） 示例传动")
    return company, [company + "\n2026年半年度报告", *([""] * 5), profile]


def test_full_joint_stock_legal_suffix_keeps_same_page_code_verification(joint_stock_identity_pages):
    company, pages = joint_stock_identity_pages
    found, page, quote = source._identity(pages, SYMBOL)
    assert found == company and page == 7
    assert company in quote and SYMBOL in quote
    assert quote in source._compact(pages[page - 1])


def test_joint_stock_identity_never_uses_short_name_or_cross_page_code(joint_stock_identity_pages):
    company, pages = joint_stock_identity_pages
    with pytest.raises(source.SourceError):
        source._identity([pages[0], f"股票简称 示例传动 股票代码 {SYMBOL}"], SYMBOL)
    with pytest.raises(source.SourceError):
        source._identity([f"股票简称 示例传动 股票代码 {SYMBOL}"], SYMBOL)
    assert company not in f"股票简称 示例传动 股票代码 {SYMBOL}"


@pytest.mark.parametrize("foreign_code", ["000002", "1000001", "0000019"])
def test_joint_stock_foreign_or_substring_code_is_rejected(joint_stock_identity_pages, foreign_code):
    _, pages = joint_stock_identity_pages
    pages[6] = pages[6].replace(SYMBOL, foreign_code)
    with pytest.raises(source.SourceError):
        source._identity(pages, SYMBOL)


def row(announcement_id="12345", title="2026年半年度报告", day="2026-08-26"):
    dt = datetime.fromisoformat(day).replace(tzinfo=source.BJ)
    return {"secCode": SYMBOL, "orgId": "org123", "announcementId": announcement_id,
            "announcementTitle": title, "announcementTime": int(dt.timestamp() * 1000),
            "adjunctType": "PDF", "adjunctUrl": f"finalpage/{day}/{announcement_id}.PDF"}


@pytest.fixture
def prepared(monkeypatch):
    monkeypatch.setattr(source, "MIN_REQUEST_INTERVAL", 0)
    monkeypatch.setattr(source, "_now", lambda: AT.astimezone(timezone.utc))
    monkeypatch.setattr(source, "_parse_pdf", lambda path: {"pages": [BODY], "truncated": False})


def transport(rows=None, *, identity=None, pdf=b"%PDF-test", recent=None):
    rows = [row()] if rows is None else rows
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path.endswith("topSearch/query"):
            value = [{"code": SYMBOL, "category": "A股", "orgId": "org123", "zwjc": "示例科技"}]
            return httpx.Response(200, json=value if identity is None else identity)
        if request.url.path.endswith("hisAnnouncement/query"):
            # Report query and optional recent direct notices are separate scopes.
            current = recent if b"searchkey=&" in request.content else rows
            if current is None:
                current = []
            return httpx.Response(200, json={"announcements": current or None,
                                             "totalAnnouncement": len(current), "hasMore": False})
        return httpx.Response(200, content=pdf)

    return httpx.MockTransport(handler), calls


def collect(tmp_path, network):
    with httpx.Client(transport=network) as client:
        return source.collect_company(SYMBOL, as_of=AT, root=tmp_path, client=client)


def test_company_is_discovered_and_pdf_identity_and_quotes_are_bound(tmp_path, prepared):
    network, calls = transport()
    result = collect(tmp_path, network)
    assert result["status"] == "ready"
    assert result["company_name"] == COMPANY
    assert datetime.fromisoformat(result["checked_at"]) >= datetime.fromisoformat(result["documents"][0]["received_at"])
    doc = result["documents"][0]
    assert doc["published_at"] == "2026-08-26T23:59:59+08:00"
    assert doc["published_precision"] == "day"
    assert COMPANY in doc["identity_quote"] and SYMBOL in doc["identity_quote"]
    assert all(q["quote"] in source._compact(BODY) for q in doc["excerpts"])
    assert {q["kind"] for q in doc["excerpts"]} >= {"business", "product", "customer", "order", "risk"}
    assert result["provenance"]["point_in_time_first_seen_verified"] is False
    assert len([c for c in calls if c.method == "GET"]) == 1


def test_warm_cache_does_not_query_or_download_again(tmp_path, prepared):
    network, calls = transport()
    original = collect(tmp_path, network)
    before = len(calls)
    cached = collect(tmp_path, network)
    assert cached["cache_hit"] is True and len(calls) == before
    assert cached["documents"] == original["documents"]
    assert len(list(tmp_path.glob("*.pdf"))) == 1


def test_cached_original_drift_fails_without_redownload(tmp_path, prepared):
    network, calls = transport()
    collect(tmp_path, network)
    (tmp_path / "12345.pdf").write_bytes(b"changed")
    before = len(calls)
    result = collect(tmp_path, network)
    assert result["status"] == "failed" and not result["documents"]
    assert len(calls) == before
    assert any("drift" in value for value in result["missing"])


def test_symlink_manifest_is_not_read_overwritten_or_followed(tmp_path, prepared):
    target = tmp_path / "private.json"
    target.write_text('{"private":"unchanged"}')
    manifest = tmp_path / f"{SYMBOL}.manifest.json"
    manifest.symlink_to(target)
    network, calls = transport()
    result = collect(tmp_path, network)
    assert result["status"] == "failed" and not calls
    assert manifest.is_symlink() and target.read_text() == '{"private":"unchanged"}'


@pytest.mark.parametrize("scope", ["root", "metadata"])
def test_symlink_cache_directory_is_rejected(tmp_path, scope):
    target = tmp_path / "elsewhere"
    target.mkdir()
    root = tmp_path / "cache"
    if scope == "root":
        root.symlink_to(target, target_is_directory=True)
    else:
        root.mkdir()
        (root / "metadata").symlink_to(target, target_is_directory=True)
    with pytest.raises(source.SourceError, match="symlink"):
        source.collect_company(SYMBOL, as_of=AT, root=root)
    assert not list(target.iterdir())


def test_orphaned_original_receipt_is_kept_without_redownload(tmp_path, prepared):
    stamp = tmp_path / "12345.received.json"
    stamp.write_text('{"recovery":"keep"}')
    network, calls = transport()
    result = collect(tmp_path, network)
    assert result["status"] == "failed"
    assert not any(c.method == "GET" for c in calls)
    assert stamp.read_text() == '{"recovery":"keep"}'
    assert any("receipt conflict" in x for x in result["missing"])


def test_valid_null_empty_is_missing_not_no_business(tmp_path, prepared):
    network, calls = transport(rows=[])
    result = collect(tmp_path, network)
    assert result["status"] == "missing" and result["documents"] == []
    assert any("不代表公司没有业务" in value for value in result["missing"])
    assert not any(c.method == "GET" for c in calls)


@pytest.mark.parametrize("change", ["foreign", "duplicate", "null_nonzero", "count_drift"])
def test_invalid_source_envelope_never_silently_becomes_empty(change):
    rows = [row()]
    total = 1
    if change == "foreign":
        rows[0]["secCode"] = "000002"
    elif change == "duplicate":
        rows.append(row())
        total = 2
    elif change == "null_nonzero":
        rows = None
    else:
        total = 2
    with pytest.raises(source.SourceError):
        source._rows({"totalAnnouncement": total, "hasMore": False, "announcements": rows}, SYMBOL, "org123")


def test_foreign_pdf_does_not_reuse_news_identity(tmp_path, prepared, monkeypatch):
    monkeypatch.setattr(source, "_parse_pdf", lambda path: {"pages": [BODY.replace(SYMBOL, "000002")]})
    network, _ = transport()
    result = collect(tmp_path, network)
    assert result["status"] == "failed" and not result["documents"]
    assert any("identity" in value or "same page" in value for value in result["missing"])


def test_future_day_precision_is_not_backfilled_to_midnight(tmp_path, prepared):
    network, calls = transport(rows=[row(day="2026-10-20")])
    result = collect(tmp_path, network)
    assert result["status"] == "missing" and not result["documents"]
    assert any("不倒填" in value for value in result["missing"])
    assert not any(c.method == "GET" for c in calls)


@pytest.mark.parametrize("pdf", [b"<html>login required</html>", b"x" * (source.MAX_PDF + 1)])
def test_non_pdf_and_oversized_original_are_failures(tmp_path, prepared, pdf):
    network, _ = transport(pdf=pdf)
    result = collect(tmp_path, network)
    assert result["status"] == "failed" and not result["documents"]
    assert not list(tmp_path.glob("*.pdf"))


@pytest.mark.parametrize("status", [302, 429])
def test_no_redirect_or_rate_limit_retry(tmp_path, prepared, status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, headers={"Location": "http://127.0.0.1/secret"})

    result = collect(tmp_path, httpx.MockTransport(handler))
    assert result["status"] == "failed" and len(calls) == 1
    assert str(status) in result["missing"][0]


def test_sentence_extraction_has_per_company_budget_and_explicit_missing():
    page = "\n".join(f"公司主营业务为第{i}类产品研发生产及配套服务，为国内外客户提供解决方案。" for i in range(20))
    values, missing = source._excerpts([page])
    assert len(values) <= 12
    assert all(x["quote"] in source._compact(page) for x in values)
    assert any("截断" in x for x in missing)
    assert any("不代表" in x for x in missing)


@pytest.mark.parametrize("kind,quote", [
    ("customer", "公司建立了以客户为中心的项目管理机制，将项目立项与资源配置、实际应用需求紧密对接。"),
    ("customer", "公司始终坚持以客户为中心，不断提升管理与服务水平，公正保护客户合法权益。"),
    ("order", "根据公司与示例财务公司签订的《金融服务框架性协议》，财务公司为本公司提供存款、结算、信贷和票据等金融服务。"),
    ("order", "董事会同意为子公司采购业务提供连带责任保证，并授权董事长与相关方签订担保协议等有关文件。"),
    ("order", "本人承诺未来关联交易将依法签订协议，确保交易定价公允，不损害上市公司与股东的合法权益。"),
    ("order", "对无法避免或者有合理原因而发生的关联交易，将遵循市场公平、公允、等价有偿等原则确定交易价格，并依法签订协议，履行合法程序，保证不损害上市公司及股东的合法权益。"),
    ("order", "公司与示例有限合伙企业签订《之合伙协议》，出资参与股权投资，不属于产品销售业务。"),
    ("order", "全体合伙人均以货币方式实缴出资，该合伙企业已完成募集和基金备案，与被投公司及相关方签署协议，对被投公司增资并完成支付。"),
    ("order", "本公司与借出方签订协议，以承担新金融负债的方式替换原金融负债，终止确认原金融负债，同时确认一项新金融负债。"),
    ("product", "公司物料采购周期与数量主要依据客户订单及产品需求确定，以适配公司以定制化产品为主的业务模式。"),
    ("risk", "尚未使用的募集资金用途及去向截至报告期期末，公司募集资金账户均已注销，余额为零元。"),
    ("risk", "公司募集资金到账金额已扣除费用，尚未支付的保荐承销费将按约定结算，净额存入募集资金账户。"),
])
def test_management_financing_and_funding_boilerplate_is_not_business_evidence(kind, quote):
    values, missing = source._excerpts([quote])
    assert not any(v["kind"] == kind for v in values)
    assert any(kind in item and "不代表" in item for item in missing)


@pytest.mark.parametrize("kind,quote", [
    ("customer", "公司坚持以客户为中心，主要客户包括制造商甲和供应商乙，报告期内持续向上述客户供应精密部件。"),
    ("customer", "报告期内主要客户采购量减少，导致公司相关业务销量下降，并对经营业绩产生不利影响。"),
    ("order", "公司与金融客户甲签订软件开发服务合同，将按约定提供系统建设服务，合同最终履行金额取决于验收。"),
    ("order", "本次关联交易为公司与客户甲签订设备销售合同，双方按公允价格成交，实际履行金额取决于后续交付验收。"),
    ("order", "公司与客户甲合伙企业签订设备销售合同，客户已完成实缴出资，公司将按约定履行设备供货义务。"),
    ("order", "公司存在金融负债，同时已与客户甲签订设备销售合同，将按约定交付设备并办理验收和结算。"),
    ("order", "公司尚未签订任何机器人产品销售合同，本次材料不构成已获得相关营业订单的证明。"),
    ("order", "公司投资开发自动化加工产线，为国内外主机厂提供高质量线束，已获得客户甲和客户乙项目定点且部分已实现供货。"),
    ("business", "现阶段公司主营业务为塑化汽车外饰件，仅获得机器人外覆盖件的小批量采购订单，订单金额占营业收入比例不足千分之一，对经营业绩影响极小。"),
    ("product", "公司产品为主控芯片及电源管理芯片，主要用于工业设备的控制与电源管理，服务下游制造企业。"),
    ("risk", "公司尚未使用的募集资金对应项目进度延期，存在后续投入不能达到预期效益及资产减值的风险。"),
    ("risk", "公司募集资金投资建设的项目尚未产生收益，目前仍处于建设阶段，后续商业化时间尚未明确。"),
    ("risk", "公司机器人业务尚未产生收入，目前仍处于送样测试阶段，后续商业化进展存在不确定性。"),
])
def test_specific_business_facts_and_negative_evidence_survive_noise_filter(kind, quote):
    values, _ = source._excerpts([quote])
    assert any(v["kind"] == kind and v["quote"] == quote for v in values)


def test_extractor_upgrade_reuses_original_and_receipt_without_pdf_download(tmp_path, prepared):
    network, calls = transport()
    original = collect(tmp_path, network)
    path = tmp_path / f"{SYMBOL}.manifest.json"
    legacy = json.loads(path.read_text())
    legacy["extractor"] = "bounded-sentences-v3"
    path.write_text(json.dumps(legacy))
    pdf = (tmp_path / "12345.pdf").read_bytes()
    receipt = (tmp_path / "12345.received.json").read_bytes()
    before = len(calls)
    refreshed = collect(tmp_path, network)
    assert refreshed["extractor"] != legacy["extractor"] and not refreshed["cache_hit"]
    assert refreshed["documents"][0]["sha256"] == original["documents"][0]["sha256"]
    assert refreshed["documents"][0]["received_at"] == original["documents"][0]["received_at"]
    assert not any(c.method == "GET" for c in calls[before:])
    assert (tmp_path / "12345.pdf").read_bytes() == pdf
    assert (tmp_path / "12345.received.json").read_bytes() == receipt


def test_table_runs_and_navigation_are_not_source_assertions():
    table = "主要业务风险单位：元项目期初期末100,000.00200,000.00300,000.00400,000.00500,000.00600,000.00700,000.00800,000.00。"
    navigation = "三、主营业务分析概述参见一、报告期内公司从事的主要业务相关内容。"
    page = table + navigation + BODY
    values, missing = source._excerpts([page])
    assert not any(table == v["quote"] or navigation == v["quote"] for v in values)
    assert any(v["kind"] == "business" and "汽车电子" in v["quote"] for v in values)
    assert any("截断" in value for value in missing)


def test_pdf_page_limit_is_rejected_in_isolated_parser(tmp_path):
    from pypdf import PdfWriter

    path = tmp_path / "too-many-pages.pdf"
    writer = PdfWriter()
    for _ in range(351):
        writer.add_blank_page(width=10, height=10)
    writer.write(path)
    with pytest.raises(source.SourceError, match="oversized PDF"):
        source._parse_pdf(path)
    assert not list(tmp_path.glob(".pdf-*"))


def test_empty_scanned_pdf_and_timeout_remain_explicit(tmp_path, prepared, monkeypatch):
    def fail(path):
        raise source.SourceError("bounded PDF parser timed out after 25 seconds")

    monkeypatch.setattr(source, "_parse_pdf", fail)
    network, _ = transport()
    result = collect(tmp_path, network)
    assert result["status"] == "failed"
    assert any("timed out" in x for x in result["missing"])


def test_private_parser_path_is_not_exposed_in_service_missing(tmp_path, prepared, monkeypatch):
    def fail(path):
        raise OSError("private-local-path/backend/.env")

    monkeypatch.setattr(source, "_parse_pdf", fail)
    network, _ = transport()
    result = collect(tmp_path, network)
    assert result["status"] == "failed"
    assert "private-local-path" not in json.dumps(result)
    assert any("OSError" in x for x in result["missing"])


def test_two_documents_share_twelve_excerpt_budget(tmp_path, prepared, monkeypatch):
    # More different full source sentences must not overflow the legacy consumer.
    pages = [BODY + BODY.replace("公司", "本公司") + BODY.replace("公司", "该公司")]
    monkeypatch.setattr(source, "_parse_pdf", lambda path: {"pages": pages})
    notice = row("12346", "关于签订重大服务合同的公告", "2026-09-01")
    network, _ = transport(recent=[notice])
    result = collect(tmp_path, network)
    assert result["status"] == "ready" and len(result["documents"]) == 2
    assert sum(len(x["excerpts"]) for x in result["documents"]) <= 12
    assert all(x["excerpts"] for x in result["documents"])


def test_parser_process_has_no_model_keys_and_isolated_python(tmp_path, monkeypatch):
    path = tmp_path / "fake.pdf"
    path.write_bytes(b"%PDF-test")
    observed = {}

    class Completed:
        returncode = 0
        pid = 123

        def __init__(self, args, **kwargs):
            observed.update(args=args, **kwargs)
            Path(args[-1]).write_text(json.dumps({"pages": [BODY], "truncated": False}))

        def poll(self):
            return 0

    monkeypatch.setenv("DEEPSEEK_API_KEY", "do-not-inherit")
    monkeypatch.setattr(subprocess, "Popen", Completed)
    assert source._parse_pdf(path)["pages"] == [BODY]
    assert observed["args"][1] == "-I"
    assert set(observed["env"]) == {"PATH", "LANG", "PYTHONUTF8"}
    assert "DEEPSEEK_API_KEY" not in observed["env"]
    assert not list(tmp_path.glob(".pdf-*"))


def test_real_blank_pdf_is_bounded_and_reports_absent_text(tmp_path):
    from pypdf import PdfWriter

    path = tmp_path / "blank.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.write(path)
    parsed = source._parse_pdf(path)
    assert parsed["pages"] == [""]
    assert "parent_sampled_rss_768MiB" in parsed["memory_limit_mode"]
    with pytest.raises(source.SourceError):
        source._identity(parsed["pages"], SYMBOL)


@pytest.mark.parametrize("symbol,as_of", [("abc", AT), ("../../000001", AT), (SYMBOL, AT.replace(tzinfo=None))])
def test_invalid_symbols_and_naive_time_rejected(tmp_path, symbol, as_of):
    with pytest.raises(ValueError):
        source.collect_company(symbol, as_of=as_of, root=tmp_path)


def test_source_timestamp_and_path_date_must_agree():
    changed = row()
    changed["announcementTime"] += int(timedelta(days=1).total_seconds() * 1000)
    with pytest.raises(source.SourceError, match="conflict"):
        source._publication(changed)


def test_empty_has_more_is_not_a_successful_empty_window():
    with pytest.raises(source.SourceError):
        source._rows({"announcements": [], "totalAnnouncement": 0, "hasMore": True}, SYMBOL, "org123")


def test_revision_report_is_eligible_and_summary_is_not_selected(tmp_path, prepared):
    revised = row(title="2026年半年度报告（修订后）")
    network, _ = transport(rows=[revised, row("12346", "2026年半年度报告摘要")])
    result = collect(tmp_path, network)
    assert result["status"] == "ready"
    assert [d["title"] for d in result["documents"]] == ["2026年半年度报告（修订后）"]


def test_operating_report_uses_halfyear_before_newer_quarter_and_keeps_two_doc_budget(tmp_path, prepared):
    # De-identified form of the actual Q3/H1/annual announcement response.
    reports = [row("12345", "2026年第三季度报告", "2026-10-17"),
               row("12346", "2026年半年度报告", "2026-08-27"),
               row("12347", "2025年年度报告", "2026-04-21")]
    notice = row("12348", "关于签订设备销售合同的公告", "2026-10-16")
    network, calls = transport(rows=reports, recent=[notice])
    result = collect(tmp_path, network)
    assert [d["announcement_id"] for d in result["documents"]] == ["12346", "12348"]
    assert result["documents"][0]["period"] == "2026-01-01至2026-06-30"
    assert any("未纳入该季度后续更新" in value for value in result["missing"])
    assert len([c for c in calls if c.method == "POST"]) == 3
    assert len([c for c in calls if c.method == "GET"]) == 2
    assert not (tmp_path / "12345.pdf").exists()


def test_operating_report_ranks_coverage_before_later_republished_old_annual(tmp_path, prepared):
    reports = [row("12345", "2025年年度报告（修订后）", "2026-10-18"),
               row("12346", "2026年半年度报告", "2026-08-27"),
               row("12347", "2026年半年度报告（修订后）", "2026-09-10")]
    network, _ = transport(rows=reports)
    result = collect(tmp_path, network)
    assert [d["announcement_id"] for d in result["documents"]] == ["12347"]


@pytest.mark.parametrize("ineligible_title,day", [
    ("2026年半年度报告", "2026-10-20"),
    ("2026年半年度报告摘要", "2026-10-18"),
    ("2026年半年度报告英文版", "2026-10-18"),
    ("关于2026年半年度募集资金使用情况的专项报告", "2026-10-18"),
], ids=["future-day", "summary", "english", "special-report"])
def test_quarterly_report_is_used_when_no_eligible_full_operating_report(tmp_path, prepared, ineligible_title, day):
    reports = [row("12345", "2026年第三季度报告", "2026-10-17"),
               row("12346", ineligible_title, day)]
    network, calls = transport(rows=reports)
    result = collect(tmp_path, network)
    assert [d["announcement_id"] for d in result["documents"]] == ["12345"]
    assert len([c for c in calls if c.method == "GET"]) == 1
    assert any("季度" in value for value in result["missing"])


def test_quarterly_report_with_financial_text_does_not_claim_format_failure(tmp_path, prepared, monkeypatch):
    text = f"{COMPANY}\n股票代码 {SYMBOL}\n一、主要财务数据\n营业收入100万元净利润20万元。"
    monkeypatch.setattr(source, "_parse_pdf", lambda path: {"pages": [text], "truncated": False})
    network, _ = transport(rows=[row(title="2026年第三季度报告", day="2026-10-17")])
    result = collect(tmp_path, network)
    assert result["status"] == "failed" and not result["documents"]
    assert any("no eligible operating excerpts" in value for value in result["missing"])
    assert not any("scanned/empty/unsupported" in value for value in result["missing"])


def test_report_policy_migrates_failed_quarter_selection_without_touching_its_original(tmp_path, prepared, monkeypatch):
    quarter = row("12345", "2026年第三季度报告", "2026-10-17")
    halfyear = row("12346", "2026年半年度报告", "2026-08-27")
    text = f"{COMPANY}\n股票代码 {SYMBOL}\n主要财务数据\n营业收入100万元净利润20万元。"
    monkeypatch.setattr(source, "_parse_pdf", lambda path: {"pages": [text if path.stem == "12345" else BODY]})
    failed = collect(tmp_path, transport(rows=[quarter])[0])
    assert failed["status"] == "failed"
    manifest = tmp_path / f"{SYMBOL}.manifest.json"
    legacy = json.loads(manifest.read_text())
    legacy.pop("report_policy", None)
    manifest.write_text(json.dumps(legacy))
    raw, receipt = (tmp_path / "12345.pdf").read_bytes(), (tmp_path / "12345.received.json").read_bytes()
    network, calls = transport(rows=[quarter, halfyear])
    result = collect(tmp_path, network)
    assert result["status"] == "ready" and not result["cache_hit"]
    assert result.get("report_policy")
    assert [d["announcement_id"] for d in result["documents"]] == ["12346"]
    assert (tmp_path / "12345.pdf").read_bytes() == raw
    assert (tmp_path / "12345.received.json").read_bytes() == receipt
    assert len([c for c in calls if c.method == "GET"]) == 1


def test_report_policy_migration_keeps_unchanged_document_identity_and_extraction_method(tmp_path, prepared):
    network, calls = transport()
    original = collect(tmp_path, network)
    manifest = tmp_path / f"{SYMBOL}.manifest.json"
    legacy = json.loads(manifest.read_text())
    legacy.pop("report_policy", None)
    manifest.write_text(json.dumps(legacy))
    before = len(calls)
    result = collect(tmp_path, network)
    assert not result["cache_hit"] and result.get("report_policy")
    assert result["documents"] == original["documents"]
    assert result["extractor"] == source.EXTRACTOR
    assert result["documents"][0]["extraction_method"] == source.EXTRACTOR
    assert not any(c.method == "GET" for c in calls[before:])


FINANCIAL_HEADING = (f"{COMPANY}2026年半年度报告\n34/143\n第八节 财务报告\n"
                     "一、审计报告\n□适用 √不适用\n二、财务报表\n合并资产负债表\n")
ACCOUNTING_ORDER = ("出售极可能发生，已经就一项出售计划作出决议且获得确定的购买承诺，"
                    "确定的购买承诺是指企业与其他方签订的具有法律约束力的购买协议，"
                    "该协议包含交易价格、时间和违约惩罚等条款。")
ACCOUNTING_RISK = ("对初始确认后信用风险显著增加的金融工具，公司按照相当于该金融工具整个存续期内"
                   "预期信用损失的金额计量其损失准备。")


def test_periodic_financial_chapter_preserves_same_page_prefix_and_physical_page():
    business = "公司主营业务为数字内容设计与展示项目实施，主要服务于文化场馆和展览设施建设。"
    order = "公司签订展览设备销售合同，合同已经生效并按实际交付和客户验收确认履约进度。"
    pages = ["目录\n第八节 财务报告 ................ 34\n" + order,
             business + "\n" + FINANCIAL_HEADING + ACCOUNTING_ORDER,
             ACCOUNTING_RISK]
    values, missing = source._excerpts(pages, periodic_report=True)
    assert any(v["quote"] == business and v["page"] == 2 for v in values)
    assert any(v["quote"].endswith(order) and v["page"] == 1 for v in values)
    assert all("购买承诺" not in v["quote"] and "损失准备" not in v["quote"] for v in values)
    assert any("第2页" in value and "财务附注" in value for value in missing)


@pytest.mark.parametrize("navigation", [
    "目录\n第八节 财务报告 ................ 34\n",
    "目录\n第八节 财务报告\n34\n一、审计报告\n二、财务报表\n",
    "具体会计信息详见第八节财务报告，本文仍为经营情况说明。\n",
    "第八节 财务报告\n本页只有重复章节页眉，没有审计及报表正文标题。\n",
], ids=["toc-leader", "toc-wrapped-page", "narrative-reference", "running-header"])
def test_periodic_financial_navigation_does_not_end_business_coverage(navigation):
    quote = "主要客户包括汽车制造商及一级供应商，本段未按产品或型号披露具体客户。"
    values, missing = source._excerpts([navigation, quote], periodic_report=True)
    assert any(v["quote"] == quote and v["page"] == 2 for v in values)
    assert any("未定位" in value and "财务报告" in value for value in missing)


def test_periodic_financial_boundary_outside_parsed_window_stays_unknown():
    quote = "公司主营业务为汽车外饰件的研发生产和销售，本报告期内主营业务未发生重大变化。"
    pages = ["目录\n第八节 财务报告 ................ 71\n", *([""] * 68), quote]
    values, missing = source._excerpts(pages, periodic_report=True)
    assert any(v["quote"] == quote and v["page"] == 70 for v in values)
    assert any("未定位" in value for value in missing)
    assert not any(value.startswith("已从") for value in missing)


@pytest.mark.parametrize("initial_heading", ["一、审计报告", "财务报表"])
def test_periodic_financial_boundary_handles_long_annual_audit_before_later_statements(initial_heading):
    business = "公司主营业务为数字内容设计与展示项目实施，主要服务于文化场馆和展览设施建设。"
    # Annual audits can span pages. The initial chapter heading does not require
    # the later financial statements to occur in a short arbitrary text window.
    title = "第十节 财务报告\n" + initial_heading + "\n"
    audit = "我们依照中国注册会计师审计准则执行审计工作，相关意见与依据如下。" * 35
    values, missing = source._excerpts([
        business + "\n" + title + audit, ACCOUNTING_ORDER,
        "二、财务报表\n合并资产负债表\n" + ACCOUNTING_RISK], periodic_report=True)
    assert any(v["quote"] == business and v["page"] == 1 for v in values)
    assert all("购买承诺" not in v["quote"] and "损失准备" not in v["quote"] for v in values)
    assert any(value.startswith("已从第1页") for value in missing)


def test_independent_notice_keeps_contract_and_denial_without_report_boundary_claim():
    quote = ("公司签订软件服务合同，合同金额未经审计并以财务报告为准，"
             "该项目尚未获得后续批量订单，不代表已形成确定收入。")
    values, missing = source._excerpts([quote], periodic_report=False)
    assert any(v["kind"] == "order" and v["quote"] == quote for v in values)
    assert not any("财务报告章节" in value or "财务附注" in value for value in missing)


def test_collect_periodic_report_applies_financial_boundary(tmp_path, prepared, monkeypatch):
    monkeypatch.setattr(source, "_parse_pdf", lambda path: {
        "pages": [BODY, FINANCIAL_HEADING, ACCOUNTING_ORDER], "truncated": False})
    result = collect(tmp_path, transport()[0])
    assert result["status"] == "ready"
    assert all("购买承诺" not in x["quote"] for d in result["documents"] for x in d["excerpts"])
    assert any("财务附注" in value for value in result["missing"])


def test_sampled_rss_guard_kills_only_its_own_parser(tmp_path, monkeypatch):
    path = tmp_path / "fake.pdf"
    path.write_bytes(b"%PDF-test")
    children = []

    class Child:
        pid = 123
        returncode = None

        def __init__(self, *args, **kwargs):
            children.append(self)

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            if self.returncode is None:
                raise subprocess.TimeoutExpired("parser", timeout)
            return self.returncode

        def kill(self):
            self.returncode = -9

    monkeypatch.setattr(subprocess, "Popen", Child)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, "999999999", ""))
    with pytest.raises(source.SourceError, match="sampled RSS"):
        source._parse_pdf(path)
    assert len(children) == 1 and children[0].returncode == -9
    assert not list(tmp_path.glob(".pdf-*"))
