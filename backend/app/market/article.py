"""资讯正文抓取（资讯弹窗的后端通道）。

弹窗化展示的第一步：把外链页面的正文抓回来重新排版，替代 iframe 嵌入。
支持两类源（域名白名单，其余一律拒绝——既是 SSRF 防护也是版权边界）：

- 新闻/快讯 ``finance.eastmoney.com/a/{code}.html`` → HTML ``#ContentBody`` 段落
  （实测该页正文为服务端直出，无需执行 JS）；
- 公告 ``data.eastmoney.com/notices/detail/{symbol}/{art_code}.html`` →
  np-cnotice-stock 官方 JSON API（详情页本身是 JS 渲染壳，解析 API 比解析
  页面稳定得多；正文分页时只取第 1 页并标注 truncated）。

抓取失败抛 ArticleFetchError → 路由转 502 → 前端降级为「摘要 + 原文链接」。
正文仅进程内 TTL 缓存（约 10 分钟），不落盘——版权上做最小化使用：
标注来源、保留原文链接、不篡改正文、不长期存储。
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

import httpx

ALLOWED_HOSTS = {"finance.eastmoney.com", "data.eastmoney.com"}

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

# 公告正文单页约 5000 字符，足够弹窗首屏；更多内容引导看原文 PDF
_MAX_NOTICE_CHARS = 20_000
_MAX_PARAGRAPHS = 120

_ART_CODE_RE = re.compile(r"/notices/detail/[^/]+/([A-Za-z0-9]+)\.html")
_TIME_RE = re.compile(r"(\d{4}年\d{2}月\d{2}日 \d{2}:\d{2})")
_SOURCE_RE = re.compile(r"文章来源：([^<\s][^<]*?)\s*(?:郑重声明|举报|</|$)")


class ArticleFetchError(Exception):
    """抓取或解析失败（网络断/结构变更/不支持的源）。"""


def classify_url(url: str) -> tuple[str, str | None]:
    """校验 URL 并分类，返回 (kind, art_code)。非法来源抛 ValueError。

    kind: "notice"（公告详情页）| "news"（新闻/快讯文章页）。
    """
    try:
        parsed = urlparse(url)
    except ValueError as exc:
        raise ValueError(f"URL 无法解析：{exc}") from exc
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("仅支持 http(s) 链接")
    host = parsed.hostname.lower()
    if host not in ALLOWED_HOSTS:
        raise ValueError(f"不支持的内容源：{host}")
    if host == "data.eastmoney.com":
        m = _ART_CODE_RE.search(parsed.path)
        if not m:
            raise ValueError("公告链接中未找到公告代码")
        return "notice", m.group(1)
    return "news", None


def _strip_tags(fragment: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", fragment)
    text = re.sub(r"<[^>]+>", "", text)
    return text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").strip()


def parse_news_html(html: str) -> dict[str, Any]:
    """从新闻文章页 HTML 提取标题/来源/时间/正文段落。

    正文容器为 ``<div id="ContentBody">``（class txtinfos）；空段落与页尾
    免责声明丢弃。结构变更（容器缺失或正文为空）抛 ArticleFetchError——
    绝不静默返回空正文伪装成功。
    """
    m = re.search(r"<title>(.*?)</title>", html, re.S)
    title = _strip_tags(m.group(1)) if m else ""
    title = re.sub(r"\s*[_-]\s*东方财富网\s*$", "", title)

    body_m = re.search(r'<div[^>]*id="ContentBody"[^>]*>(.*?)<!--\s*正文中部', html, re.S)
    if not body_m:
        # 容器缺失是结构变更信号，显式失败走降级
        raise ArticleFetchError("文章页正文容器未找到（页面结构可能已变更）")
    raw_paragraphs = re.findall(r"<p[^>]*>(.*?)</p>", body_m.group(1), re.S)
    paragraphs: list[str] = []
    for raw in raw_paragraphs:
        text = _strip_tags(raw)
        if not text:
            continue
        if text.startswith("免责声明"):
            continue
        paragraphs.append(text)
    if not paragraphs:
        raise ArticleFetchError("文章正文为空（可能被反爬拦截）")

    time_m = _TIME_RE.search(html)
    source_m = _SOURCE_RE.search(html)
    return {
        "title": title or None,
        "source_label": source_m.group(1).strip() if source_m else None,
        "published": time_m.group(1) if time_m else None,
        "paragraphs": paragraphs[:_MAX_PARAGRAPHS],
        "truncated": False,
    }


def parse_notice_payload(data: dict[str, Any]) -> dict[str, Any]:
    """把公告全文 API payload 转为统一形态（按行分段，超长截断）。"""
    content = data.get("notice_content") or ""
    if not content.strip():
        raise ArticleFetchError("公告正文为空（可能仅有 PDF 附件）")
    paragraphs = [line.strip() for line in content.splitlines() if line.strip()]
    total = sum(len(p) for p in paragraphs)
    truncated = False
    if len(paragraphs) > _MAX_PARAGRAPHS:
        paragraphs = paragraphs[:_MAX_PARAGRAPHS]
        truncated = True
    if total > _MAX_NOTICE_CHARS:
        acc, kept = 0, []
        for p in paragraphs:
            if acc + len(p) > _MAX_NOTICE_CHARS:
                truncated = True
                break
            kept.append(p)
            acc += len(p)
        paragraphs = kept
    return {
        "title": data.get("notice_title") or None,
        "source_label": "巨潮资讯·东方财富",
        "published": None,
        "paragraphs": paragraphs,
        "truncated": truncated,
    }


async def fetch_article(url: str) -> dict[str, Any]:
    """按 URL 类型抓取正文，返回统一形态 {kind,title,source_label,published,paragraphs,truncated}。"""
    kind, art_code = classify_url(url)
    async with httpx.AsyncClient(
        trust_env=False,  # 直连，不走系统代理（本机会话代理会伪装失败）
        headers={"User-Agent": _UA, "Connection": "close"},  # 东财 keep-alive 会被断
        timeout=10.0,
        follow_redirects=True,
    ) as client:
        if kind == "notice":
            api = "https://np-cnotice-stock.eastmoney.com/api/content/ann"
            resp = await client.get(
                api,
                params={"art_code": art_code, "client_source": "web", "page_index": "1"},
                headers={"User-Agent": _UA, "Referer": "https://data.eastmoney.com/", "Connection": "close"},
            )
            if resp.status_code != 200:
                raise ArticleFetchError(f"公告全文接口 HTTP {resp.status_code}")
            payload = resp.json()
            data = (payload or {}).get("data") or {}
            if not data:
                raise ArticleFetchError("公告全文接口返回空数据")
            result = parse_notice_payload(data)
        else:
            resp = await client.get(url)
            if resp.status_code != 200:
                raise ArticleFetchError(f"文章页 HTTP {resp.status_code}")
            result = parse_news_html(resp.text)
    result["kind"] = kind
    result["url"] = url
    return result
