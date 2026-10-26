"""Stateless decoder for Eastmoney's distinct public board-changes product.

The board_flow owner owns HTTP, single-flight, caching and persistence.  zjl is
the official changes page's main-net amount in wan CNY; it is not asserted to
be numerically interchangeable with clist f62 or a trade/selection signal.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
import hashlib
import html
import json
from math import isfinite
import re

from app.core.bjtime import BJ_TZ

CHANGES_URL = "https://push2ex.eastmoney.com/getAllBKChanges"
DIRECTORY_URL = "https://data.eastmoney.com/bkzj/hy.html"
PAGE_SIZE = 50
MAX_PAGES = 23


def decode_directory(text: str) -> dict[str, dict[str, str]]:
    """Use the source's three labelled groups; never infer kind from a name."""
    labels = re.search(r'<ul[^>]*class="pop_slider"[^>]*>(.*?)</ul>', text, re.S)
    blocks = re.findall(r'<div[^>]*class="pop-cont[^\"]*"[^>]*>(.*?)</div>', text, re.S)
    if not labels or re.findall(r'<li[^>]*>\s*([^<]+?)\s*</li>', labels[1]) != ["行业", "概念", "地域"] or len(blocks) != 3:
        raise ValueError("正式板块目录分组未能核实")
    out, seen = {}, set()
    for kind, block in zip(("industry", "concept", "region"), blocks):
        rows = re.findall(r'<a\s+href="/bkzj/(BK\d{3,4})\.html"[^>]*>([^<]+)</a>', block)
        if not rows:
            raise ValueError("正式板块目录类别为空")
        out[kind] = {}
        for code, name in rows:
            name = html.unescape(name).strip()
            if code in seen or not name:
                raise ValueError("正式板块目录身份重复或名称缺失")
            seen.add(code)
            out[kind][code] = name
    return out


def _number(value) -> float | None:
    if value is None or value == "-":
        return None
    if isinstance(value, bool):
        raise ValueError("异动统计含布尔数值")
    try:
        result = float(value)
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError("异动统计含非法数值") from exc
    if not isfinite(result):
        raise ValueError("异动统计含非有限数值")
    return result


def build_snapshot(pages: list[dict], directory: dict, received_at: datetime, receipts: list[dict]) -> dict:
    """Require every source page from one batch; unknown identities stay counted."""
    first = pages[0]
    total, stamp = first.get("tc"), str(first.get("dt", ""))
    if isinstance(total, bool) or not isinstance(total, int) or total <= 0:
        raise ValueError("异动统计总量无效")
    count = (total + PAGE_SIZE - 1) // PAGE_SIZE
    if count > MAX_PAGES or len(pages) != count:
        raise ValueError("异动统计超出有界分页或分页不完整")
    try:
        source_at = datetime.strptime(stamp, "%Y%m%d%H%M%S").replace(tzinfo=BJ_TZ)
    except ValueError as exc:
        raise ValueError("异动统计批次钟缺失或无效") from exc
    if source_at.strftime("%Y%m%d%H%M%S") != stamp or source_at > received_at:
        raise ValueError("异动统计批次钟异常或在未来")
    by_kind = {kind: [] for kind in ("industry", "concept")}
    known = {code: kind for kind, rows in directory.items() for code in rows}
    seen, unknown, excluded, name_mismatch = set(), [], [], []
    canonical = []
    for index, page in enumerate(pages):
        expected = min(PAGE_SIZE, total - index * PAGE_SIZE)
        raw = page.get("allbk")
        if page.get("tc") != total or str(page.get("dt")) != stamp or not isinstance(raw, list) or len(raw) != expected:
            raise ValueError("异动统计分页数量或批次不一致，未混合展示")
        for row in raw:
            code, name = row.get("c"), row.get("n")
            if not isinstance(code, str) or not re.fullmatch(r"BK\d{3,4}", code) or code in seen or not isinstance(name, str) or not name.strip():
                raise ValueError("异动统计板块身份缺失或重复")
            seen.add(code)
            main_wan, change = _number(row.get("zjl")), _number(row.get("u"))
            canonical.append([code, name, main_wan, change])
            kind = known.get(code)
            if kind is None:
                unknown.append(code)
                continue
            if name != directory[kind][code]:
                name_mismatch.append(code)
                continue
            if kind == "region":
                excluded.append(code)
                continue
            by_kind[kind].append({
                "board_code": code, "name": name, "kind": kind,
                "change_pct": change, "main_net_yi": None if main_wan is None else main_wan / 10000,
                "main_net_ratio": None, "super_net_yi": None, "main_net_5d_yi": None,
                "main_net_10d_yi": None, "main_net_20d_yi": None,
                "leader_name": None, "leader_symbol": None, "streak": None, "rank_delta": None,
            })
    for rows in by_kind.values():
        rows.sort(key=lambda r: (r["main_net_yi"] is None, -(r["main_net_yi"] or 0), r["board_code"]))
        for rank, row in enumerate(rows, 1):
            row["rank"] = rank
    digest = hashlib.sha256(json.dumps(canonical, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    return {"version": 1, "source": "eastmoney_board_changes", "source_as_of": source_at.isoformat(),
            "received_at": received_at.isoformat(), "as_of": source_at.date().isoformat(),
            "source_total": total, "source_returned": len(seen), "source_pages": count,
            "directory_counts": {k: len(v) for k, v in directory.items()}, "rows": by_kind,
            "unknown_codes": unknown, "excluded_region_codes": excluded, "name_mismatch_codes": name_mismatch,
            "normalized_sha256": digest, "source_receipts": receipts}


async def fetch_snapshot(http, now, *, seed: tuple[dict, dict] | None = None) -> dict:
    """At most 23 pages plus one directory, no retries; 22s whole-read bound."""
    receipts = []

    async def get(url, params=None):
        async with http.stream("GET", url, params=params, headers={"Referer": "https://quote.eastmoney.com/"}) as response:
            response.raise_for_status()
            parts, size = [], 0
            async for part in response.aiter_bytes():
                size += len(part)
                if size > 524288:
                    raise ValueError("公开来源响应超过有界大小")
                parts.append(part)
            raw = b"".join(parts)
        receipts.append({"url": url, "page": (params or {}).get("pageindex"),
                         "received_at": now().isoformat(), "sha256": hashlib.sha256(raw).hexdigest()})
        return raw

    async def page(index):
        raw = await get(CHANGES_URL, {"ut": "7eea3edcaed734bea9cbfc24409ed989", "dpt": "wzchanges",
                                          "pageindex": str(index), "pagesize": str(PAGE_SIZE)})
        data = json.loads(raw).get("data")
        if not isinstance(data, dict):
            raise ValueError("异动统计来源未提供有效数据")
        return data

    async with asyncio.timeout(22):
        if seed is None:
            first = await page(0)
        else:
            first, receipt = seed
            receipts.append(dict(receipt))
        total = first.get("tc")
        if isinstance(total, bool) or not isinstance(total, int) or total <= 0 or total > PAGE_SIZE * MAX_PAGES:
            raise ValueError("异动统计总量无效或超过有界分页")
        semaphore = asyncio.Semaphore(4)

        async def bounded(index):
            async with semaphore:
                return await page(index)

        # Each already-paid source response is used once. Source clocks must
        # match at build time; completion order cannot merge different batches.
        rest = await asyncio.gather(*(bounded(i) for i in range(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)), return_exceptions=True)
        for result in rest:
            if isinstance(result, BaseException):
                raise result
        directory_raw = await get(DIRECTORY_URL)
        directory = decode_directory(directory_raw.decode("utf-8"))
    return build_snapshot([first, *rest], directory, now(), receipts)
