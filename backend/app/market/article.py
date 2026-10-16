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

import asyncio
import ipaddress
import re
import socket
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

ALLOWED_HOSTS = {"finance.eastmoney.com", "data.eastmoney.com"}
_FETCH_HOSTS = ALLOWED_HOSTS | {"np-cnotice-stock.eastmoney.com"}
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_MAX_REDIRECTS = 3

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
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("正文链接不支持登录凭据")
    if parsed.port not in (None, 80 if parsed.scheme == "http" else 443):
        raise ValueError("正文链接仅支持标准端口")
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


# 正文内除 <p> 外还有真实内容结构：表格（数据类文章的排行榜/涨跌榜）与配图。
# 只抓 <p> 会把「夹在段落间」的表格整块丢掉、把「嵌在 <p> 里」的表格剥成一串
# 数字挤一行（2026-09-04 用户反馈），因此解析为有序块（p/table/img）。
_TOKEN_RE = re.compile(r"<table\b.*?</table>|<img\b[^>]*/?>", re.S)
_TR_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_TD_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S)
_SRC_RE = re.compile(r"""src=["']([^"']+)["']""")
_MAX_TABLE_ROWS = 100


def _normalize_img_src(src: str) -> str | None:
    """协议相对地址补全；data: URI 与无协议地址丢弃（弹窗内不内嵌）。"""
    src = src.strip()
    if not src or src.startswith("data:"):
        return None
    if src.startswith("//"):
        return "https:" + src
    if not src.startswith(("http://", "https://")):
        return None
    return src


def _parse_table_block(frag: str) -> dict[str, Any]:
    rows: list[list[str]] = []
    for tr in _TR_RE.finditer(frag):
        cells = [c.strip() for c in (_strip_tags(td.group(1)) for td in _TD_RE.finditer(tr.group(1)))]
        # 全空行（排版用）丢弃；部分空单元格保留（位置对齐）
        if any(cells):
            rows.append(cells)
    truncated_rows = False
    if len(rows) > _MAX_TABLE_ROWS:
        rows = rows[:_MAX_TABLE_ROWS]
        truncated_rows = True
    # 首行是 <th> → 表头行
    first_tr = _TR_RE.search(frag)
    header = bool(first_tr and "<th" in first_tr.group(0))
    return {"type": "table", "rows": rows, "header": header, "truncated_rows": truncated_rows}


def _parse_img_block(frag: str) -> dict[str, Any] | None:
    src_m = _SRC_RE.search(frag)
    src = _normalize_img_src(src_m.group(1)) if src_m else None
    return {"type": "img", "src": src} if src else None


def _rich_blocks(fragment: str) -> list[dict[str, Any]]:
    """把一段 HTML（<p> 内部或正文裸片段）拆成有序块：文本 + 内嵌 table/img。"""
    blocks: list[dict[str, Any]] = []
    pos = 0
    for m in _TOKEN_RE.finditer(fragment):
        text = _strip_tags(fragment[pos : m.start()])
        if text:
            blocks.append({"type": "p", "text": text})
        frag = m.group(0)
        block = _parse_table_block(frag) if frag.startswith("<table") else _parse_img_block(frag)
        if block is not None:
            blocks.append(block)
        pos = m.end()
    text = _strip_tags(fragment[pos:])
    if text:
        blocks.append({"type": "p", "text": text})
    return blocks


def parse_news_html(html: str) -> dict[str, Any]:
    """从新闻文章页 HTML 提取标题/来源/时间/正文有序块（p/table/img）。

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

    # 按文档顺序整体扫描：段落间裸表格/图片、嵌在 <p> 里的表格都在同一条时间线上
    blocks: list[dict[str, Any]] = []
    pos = 0
    raw = body_m.group(1)
    for m2 in re.finditer(r"<p\b[^>]*>(.*?)</p>", raw, re.S):
        blocks.extend(_rich_blocks(raw[pos : m2.start()]))  # 段落间的裸 table/img
        blocks.extend(_rich_blocks(m2.group(1)))  # 段内文本 + 内嵌 table/img
        pos = m2.end()
    blocks.extend(_rich_blocks(raw[pos:]))  # 尾部裸块

    blocks = [b for b in blocks if b["type"] != "p" or (b["text"] and not b["text"].startswith("免责声明"))]
    truncated = False
    if len(blocks) > _MAX_PARAGRAPHS:
        blocks = blocks[:_MAX_PARAGRAPHS]
        truncated = True
    if not any(b["type"] == "p" or (b["type"] == "table" and b["rows"]) for b in blocks):
        raise ArticleFetchError("文章正文为空（可能被反爬拦截）")
    paragraphs = [b["text"] for b in blocks if b["type"] == "p"]

    time_m = _TIME_RE.search(html)
    source_m = _SOURCE_RE.search(html)
    return {
        "title": title or None,
        "source_label": source_m.group(1).strip() if source_m else None,
        "published": time_m.group(1) if time_m else None,
        "paragraphs": paragraphs,
        "blocks": blocks,
        "truncated": truncated,
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
        "blocks": [{"type": "p", "text": p} for p in paragraphs],
        "truncated": truncated,
    }


async def _public_target(url: str) -> tuple[httpx.URL, str]:
    """Validate every hop and connect to the checked IP, retaining TLS identity.

    Pinning the address prevents a second DNS lookup from bypassing the check.
    HTTPX/httpcore's documented sni_hostname extension preserves certificate checks.
    """
    try:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if (parsed.scheme not in ("http", "https") or host not in _FETCH_HOSTS
                or parsed.username is not None or parsed.password is not None
                or port != (443 if parsed.scheme == "https" else 80)):
            raise ValueError("正文跳转超出允许来源")
        addresses = await asyncio.to_thread(socket.getaddrinfo, host, port,
                                             type=socket.SOCK_STREAM)
        ips = [ipaddress.ip_address(row[4][0]) for row in addresses]
        if not ips or any(not ip.is_global for ip in ips):
            raise ValueError("正文来源未解析到公网地址")
        # Prefer IPv4 where supplied; do not fall back to an unchecked DNS lookup.
        address = next((ip for ip in ips if ip.version == 4), ips[0])
        return httpx.URL(url).copy_with(host=str(address)), host
    except (ValueError, OSError) as exc:
        raise ArticleFetchError(str(exc)) from exc


async def _fetch_response(client: httpx.AsyncClient, url: str) -> httpx.Response:
    current = url
    visited: set[str] = set()
    for _ in range(_MAX_REDIRECTS + 1):
        if current in visited:
            raise ArticleFetchError("正文跳转循环")
        visited.add(current)
        target, host = await _public_target(current)
        async with client.stream("GET", target, headers={"Host": host},
                                 extensions={"sni_hostname": host}) as response:
            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("location")
                if not location:
                    raise ArticleFetchError("正文跳转缺少目标")
                next_url = urljoin(current, location)
                if current.startswith("https:") and not next_url.startswith("https:"):
                    raise ArticleFetchError("正文跳转不允许降为明文连接")
                current = next_url
                continue
            if response.status_code != 200:
                raise ArticleFetchError(f"正文接口 HTTP {response.status_code}")
            length = response.headers.get("content-length")
            if length is not None:
                try:
                    if int(length) < 0 or int(length) > _MAX_RESPONSE_BYTES:
                        raise ArticleFetchError("正文响应过大")
                except ValueError as exc:
                    raise ArticleFetchError("正文响应长度无效") from exc
            body = bytearray()
            async for chunk in response.aiter_bytes(chunk_size=65536):
                if len(body) + len(chunk) > _MAX_RESPONSE_BYTES:
                    raise ArticleFetchError("正文响应过大")
                body.extend(chunk)
            # Already decoded by aiter_bytes; omit compression headers on reconstruction.
            return httpx.Response(200, content=bytes(body),
                                  headers={"content-type": response.headers.get("content-type", "")})
    raise ArticleFetchError("正文跳转次数超限")


async def fetch_article(url: str) -> dict[str, Any]:
    """Fetch a bounded public article without automatic or unchecked redirects."""
    kind, art_code = classify_url(url)
    target = (str(httpx.URL("https://np-cnotice-stock.eastmoney.com/api/content/ann",
                           params={"art_code": art_code, "client_source": "web", "page_index": "1"}))
              if kind == "notice" else url)
    try:
        async with asyncio.timeout(10.0), httpx.AsyncClient(
            trust_env=False, headers={"User-Agent": _UA, "Connection": "close",
                                      "Referer": "https://data.eastmoney.com/"},
            timeout=10.0, follow_redirects=False,
        ) as client:
            response = await _fetch_response(client, target)
        if kind == "notice":
            payload = response.json()
            data = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(data, dict) or not data:
                raise ArticleFetchError("公告全文接口返回空数据")
            result = parse_notice_payload(data)
        else:
            result = parse_news_html(response.text)
    except (httpx.HTTPError, TimeoutError, ValueError) as exc:
        raise ArticleFetchError("正文读取失败") from exc
    result["kind"] = kind
    result["url"] = url
    return result
