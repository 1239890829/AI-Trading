"""资讯正文抓取测试：解析纯函数 + 端点路由行为（mock 网络）。

纪律：结构变更必须显式失败（ArticleFetchError），绝不静默返回空正文——
空正文被前端当"成功"渲染会是比降级更糟的假数据。
"""
from __future__ import annotations

import pytest

from app.market.article import (
    ArticleFetchError,
    classify_url,
    parse_news_html,
    parse_notice_payload,
)

NEWS_HTML = """<html><head><title>某公司龙虎榜数据(08-27) _ 东方财富网</title></head>
<body>
<div class="sourcebox"> 文章来源：东方财富Choice数据 郑重声明：
<div class="txtinfos" id="ContentBody" style="margin-top:0;">
<p>　　交易所2026年8月27日公布的交易公开信息显示，某公司当日收报<span>12.8元。</span></p>
<p></p>
<p>第二条正文内容。</p>
<p style="color:#999">免责声明：本文基于AI生产，仅供参考。</p>
<!-- 正文中部 内嵌广告 -->
<div class="ad_context3"></div></div>
<div>2026年08月27日 16:25</div>
</body></html>"""


# ---------- classify_url（白名单三态） ----------

def test_classify_news_url():
    kind, code = classify_url("https://finance.eastmoney.com/a/202608273856376263.html")
    assert kind == "news" and code is None


def test_classify_notice_url_extracts_art_code():
    kind, code = classify_url("https://data.eastmoney.com/notices/detail/000001/AN202608141827992716.html")
    assert kind == "notice"
    assert code == "AN202608141827992716"


def test_classify_rejects_non_whitelisted_host():
    with pytest.raises(ValueError):
        classify_url("https://evil.example.com/a/123.html")


def test_classify_rejects_non_http():
    with pytest.raises(ValueError):
        classify_url("file:///etc/passwd")


# ---------- parse_news_html ----------

def test_parse_news_html_extracts_paragraphs_and_meta():
    out = parse_news_html(NEWS_HTML)
    assert out["title"] == "某公司龙虎榜数据(08-27)"
    assert out["paragraphs"] == ["交易所2026年8月27日公布的交易公开信息显示，某公司当日收报12.8元。", "第二条正文内容。"]
    assert out["blocks"] == [
        {"type": "p", "text": "交易所2026年8月27日公布的交易公开信息显示，某公司当日收报12.8元。"},
        {"type": "p", "text": "第二条正文内容。"},
    ]
    assert out["source_label"] == "东方财富Choice数据"
    assert out["published"] == "2026年08月27日 16:25"
    assert out["truncated"] is False


def test_parse_news_html_extract_tables_and_images_in_order():
    """三类块按文档顺序还原：裸表格（曾整块丢失）与嵌在 <p> 里的表格（曾被压成
    一串数字挤一行，2026-09-04 用户反馈）都必须还原为真表格。"""
    html = """<html><head><title>t _ 东方财富网</title></head><body>
<div id="ContentBody">
<p>　　截至收盘，概念上涨。</p>
<table class="cms_autoformat_table"><tr><th>概念</th><th>涨跌幅</th></tr>
<tr><td>乳业</td><td>2.47</td></tr><tr><td></td><td></td></tr><tr><td>养鸡</td><td>4.57</td></tr></table>
<p>　　资金流入榜　　<table><tr><th>代码</th><th>简称</th></tr>
<tr><td><span><a>002385</a></span></td><td>大北农</td></tr></table></p>
<p>　　配图如下</p><img src="//img.eastmoney.com/news/2026/a.jpg" />
<p>　　（文章来源：证券时报网）</p>
<!-- 正文中部 --></div></body></html>"""
    out = parse_news_html(html)
    types = [b["type"] for b in out["blocks"]]
    assert types == ["p", "table", "p", "table", "p", "img", "p"]
    bare = out["blocks"][1]
    assert bare["header"] is True
    assert bare["rows"] == [["概念", "涨跌幅"], ["乳业", "2.47"], ["养鸡", "4.57"]]  # 全空行丢弃
    nested = out["blocks"][3]
    assert nested["rows"] == [["代码", "简称"], ["002385", "大北农"]]  # 单元格内链接标签已剥
    assert out["blocks"][5]["src"] == "https://img.eastmoney.com/news/2026/a.jpg"  # 协议相对补全
    assert out["paragraphs"] == [b["text"] for b in out["blocks"] if b["type"] == "p"]


def test_parse_news_html_table_row_cap_marks_truncated():
    rows = "".join(f"<tr><td>r{i}</td><td>{i}</td></tr>" for i in range(150))
    html = f'<html><body><div id="ContentBody"><table>{rows}</table><!-- 正文中部 --></div></body></html>'
    out = parse_news_html(html)
    assert out["blocks"][0]["type"] == "table"
    assert len(out["blocks"][0]["rows"]) == 100
    assert out["blocks"][0]["truncated_rows"] is True


def test_parse_news_html_missing_body_raises():
    # 结构变更 → 显式失败（前端降级），绝不静默返回空正文
    with pytest.raises(ArticleFetchError):
        parse_news_html("<html><body>没有正文容器</body></html>")


def test_parse_news_html_empty_body_raises():
    html = NEWS_HTML.replace(
        "<p>　　交易所2026年8月27日公布的交易公开信息显示，某公司当日收报<span>12.8元。</span></p>", ""
    ).replace("<p>第二条正文内容。</p>", "")
    with pytest.raises(ArticleFetchError):
        parse_news_html(html)


# ---------- parse_notice_payload ----------

def test_parse_notice_payload_splits_lines():
    out = parse_notice_payload({"notice_title": "某公司:2026年半年度报告", "notice_content": "第一段\n  第二段  \n\n第三段"})
    assert out["title"] == "某公司:2026年半年度报告"
    assert out["paragraphs"] == ["第一段", "第二段", "第三段"]
    assert out["blocks"] == [{"type": "p", "text": t} for t in ("第一段", "第二段", "第三段")]
    assert out["truncated"] is False


def test_parse_notice_payload_truncates_long_content():
    content = "\n".join(f"第{i}段" + "x" * 2000 for i in range(20))
    out = parse_notice_payload({"notice_title": "t", "notice_content": content})
    assert out["truncated"] is True
    assert sum(len(p) for p in out["paragraphs"]) <= 20_000


def test_parse_notice_payload_empty_raises():
    with pytest.raises(ArticleFetchError):
        parse_notice_payload({"notice_title": "t", "notice_content": "  \n  "})


# ---------- 端点行为（不 mock 白名单拒绝与降级路径） ----------

def test_endpoint_rejects_non_whitelisted_url(client):
    resp = client.get("/api/news/content", params={"url": "https://evil.example.com/x.html"})
    assert resp.status_code == 400


def test_endpoint_degrades_on_fetch_failure(client, monkeypatch: pytest.MonkeyPatch):
    """抓取失败 → 502；失败必须在外部边界注入，不能靠真实 DNS/公网制造。"""
    from app.api.routes import market_stock

    async def unavailable(_url: str):
        raise ArticleFetchError("test source unavailable")

    monkeypatch.setattr(market_stock, "fetch_article", unavailable)
    resp = client.get(
        "/api/news/content",
        params={"url": "https://finance.eastmoney.com/a/nonexistent000000000.html"},
    )
    assert resp.status_code == 502
    assert "test source unavailable" in resp.json()["detail"]
