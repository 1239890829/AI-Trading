"""Bounded, stateless reads of Sina's own board funds and member products.

Category identities and money definitions stay with Sina. They are never BK,
THS, f62 or a mainline/selection signal. The caller owns HTTP and caching.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import re

from app.core.bjtime import BJ_TZ


BASE_URL = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
SOURCE = "sina_board_funds"
PAGE_SIZE, MAX_PAGES, MAX_BODY_BYTES, READ_SECONDS = 100, 80, 512 * 1024, 22
_KINDS = {"industry": "0", "concept": "1"}
_YI = Decimal(100000000)
_CLOCK_UNKNOWN = "来源未给本次榜单或成员的源日/秒钟；接收钟不代表源钟"


def _clock(now):
    value = now()
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("新浪来源接收钟必须带时区")
    return value.astimezone(BJ_TZ)


def _kind(kind):
    if kind not in _KINDS:
        raise ValueError("新浪板块类别无效")
    return _KINDS[kind]


def _category(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", value):
        raise ValueError("新浪板块category身份无效")
    return value


def _symbol(value):
    if not isinstance(value, str) or not re.fullmatch(r"(?:sh(?:60|68)|sz(?:00|30))\d{4}", value):
        raise ValueError("新浪成员市场或A股身份未核实")
    return value[2:]


def _number(value, field, errors):
    if value is None or value == "" or value == "-":
        errors.append(field + "_missing")
        return None
    try:
        number = None if isinstance(value, bool) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        number = None
    if number is None or not number.is_finite() or not math.isfinite(float(number)):
        errors.append(field + "_invalid")
        return None
    return number


def _converted(value, field, errors, divisor=Decimal(1)):
    number = _number(value, field, errors)
    try:
        result = None if number is None else float(number / divisor)
    except (OverflowError, InvalidOperation):
        result = None
    if result is not None and not math.isfinite(result):
        errors.append(field + "_invalid")
        return None
    return result


async def _read(http, method, params, now, receipts):
    async with http.stream("GET", BASE_URL + method, params=params,
                           headers={"Referer": "https://finance.sina.com.cn/", "User-Agent": "Mozilla/5.0"}) as response:
        response.raise_for_status()
        parts, size = [], 0
        async for part in response.aiter_bytes():
            size += len(part)
            if size > MAX_BODY_BYTES:
                raise ValueError("新浪来源单页超过512KiB上限")
            parts.append(part)
    raw = b"".join(parts)
    received = _clock(now).isoformat()
    receipts.append({"operation": method, "page": params.get("page"), "received_at": received,
                     "sha256": hashlib.sha256(raw).hexdigest()})
    return json.loads(raw.decode("gbk"))


async def _count(http, method, params, now, receipts):
    value = await _read(http, method, params, now, receipts)
    if not isinstance(value, str) or not re.fullmatch(r"0|[1-9]\d*", value) or int(value) > PAGE_SIZE * MAX_PAGES:
        raise ValueError("新浪来源计数无效或超过8000行上限")
    return int(value)


async def _pages(http, method, params, total, now, receipts):
    semaphore = asyncio.Semaphore(4)
    async def page(index):
        async with semaphore:
            rows = await _read(http, method, {**params, "page": str(index), "num": str(PAGE_SIZE)}, now, receipts)
            expected = min(PAGE_SIZE, max(0, total - (index - 1) * PAGE_SIZE))
            if not isinstance(rows, list) or len(rows) != expected or any(not isinstance(row, dict) for row in rows):
                raise ValueError("新浪来源分页数量或结构不完整")
            return rows
    tasks = [asyncio.create_task(page(i)) for i in range(1, max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE) + 1)]
    try:
        pages = await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
    return [row for page in pages for row in page]


def _reasons(rows):
    counts = Counter(reason for row in rows for reason in row.get("degraded", []))
    return [f"{reason}:{count}" for reason, count in sorted(counts.items())]


def _board_rows(raw, kind):
    expected, rows, seen = _kind(kind), [], set()
    for rank, item in enumerate(raw, 1):
        category, name = _category(item.get("category")), item.get("name")
        if item.get("cate_type") != expected or not isinstance(name, str) or not name.strip() or category in seen:
            raise ValueError("新浪正式板块类别、名称或唯一身份不符")
        seen.add(category)
        errors = []
        leader = item.get("ts_symbol")
        try:
            leader_symbol = _symbol(leader) if leader else None
        except ValueError:
            leader_symbol = None
            errors.append("leader_identity_unknown")
        leader_name = item.get("ts_name")
        rows.append({"category": category, "name": name, "kind": kind, "rank": rank,
                     "change_pct": _converted(item.get("avg_changeratio"), "board_change", errors, Decimal("0.01")),
                     "net_inflow_yi": _converted(item.get("netamount"), "net_inflow", errors, _YI),
                     "net_inflow_ratio_pct": _converted(item.get("ratioamount"), "net_inflow_ratio", errors, Decimal("0.01")),
                     "leader_symbol": leader_symbol,
                     "leader_name": leader_name if isinstance(leader_name, str) and leader_name.strip() else None,
                     "source_as_of": None, "degraded": errors})
    return rows


async def fetch_boards(http, kind, now):
    """The complete reported Sina category list, not all market concepts."""
    fenlei, receipts = _kind(kind), []
    _clock(now)
    async with asyncio.timeout(READ_SECONDS):
        total = await _count(http, "MoneyFlow.ssc_bkzj_bk", {"fenlei": fenlei}, now, receipts)
        raw = await _pages(http, "MoneyFlow.ssl_bkzj_bk", {"sort": "netamount", "asc": "0", "fenlei": fenlei},
                           total, now, receipts)
        rows = _board_rows(raw, kind)
        if total != await _count(http, "MoneyFlow.ssc_bkzj_bk", {"fenlei": fenlei}, now, receipts):
            raise ValueError("新浪板块计数在分页期间改变")
    return {"available": True, "source": SOURCE, "kind": kind, "source_as_of": None,
            "received_at": _clock(now).isoformat(), "coverage": {"total": total, "returned": len(rows), "complete": True},
            "rows": rows, "degraded": [_CLOCK_UNKNOWN, "本源正式分类含属性集合，不等于热门题材或最强主线", *_reasons(rows)],
            "source_receipts": receipts}


def _directory_board(directory, kind, category):
    if (not isinstance(directory, dict) or directory.get("available") is not True or directory.get("source") != SOURCE
            or directory.get("kind") != kind or not isinstance(directory.get("rows"), list)):
        raise ValueError("新浪同类正式目录尚未完整核实")
    coverage, rows = directory.get("coverage"), directory["rows"]
    if (not isinstance(coverage, dict) or coverage.get("complete") is not True
            or type(coverage.get("total")) is not int or coverage["total"] != len(rows)
            or coverage.get("returned") != len(rows)):
        raise ValueError("新浪同类正式目录覆盖不完整")
    seen, found = set(), None
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("新浪正式目录身份无效")
        identity, name = _category(row.get("category")), row.get("name")
        if identity in seen or row.get("kind") != kind or not isinstance(name, str) or not name.strip():
            raise ValueError("新浪正式目录身份冲突")
        seen.add(identity)
        if identity == category:
            found = row
    if found is None:
        raise ValueError("新浪category不在该类正式目录")
    return found


def _member_rows(raw):
    rows, previous = [], None
    for item in raw:
        original, name = item.get("symbol"), item.get("name")
        symbol = _symbol(original)
        if not isinstance(name, str) or not name.strip() or previous is not None and original <= previous:
            raise ValueError("新浪成员名称无效、跨页重复或来源symbol顺序改变")
        previous = original
        errors = []
        price = _converted(item.get("trade"), "quote_price", errors)
        change = _converted(item.get("changeratio"), "quote_change", errors, Decimal("0.01"))
        if price is not None and price <= 0:
            errors.append("quote_price_nonpositive")
            price = None
        if price is None:
            change = None
        rows.append({"symbol": symbol, "source_symbol": original, "name": name, "price": price, "change_pct": change,
                     "net_inflow_yi": _converted(item.get("netamount"), "net_inflow", errors, _YI),
                     "r0_net_yi": _converted(item.get("r0_net"), "r0_net", errors, _YI),
                     "net_inflow_ratio_pct": _converted(item.get("ratioamount"), "net_inflow_ratio", errors, Decimal("0.01")),
                     "source_as_of": None, "degraded": errors})
    return rows


async def _daily(http, bankuai, now, receipts):
    try:
        raw = await _read(http, "MoneyFlow.ssl_bkzj_zjlrqs", {"page": "1", "num": "1", "sort": "opendate",
                          "asc": "0", "bankuai": bankuai}, now, receipts)
        if not isinstance(raw, list) or len(raw) != 1 or not isinstance(raw[0], dict):
            raise ValueError("新浪最新日线缺失或结构无效")
        record, received = raw[0], _clock(now)
        label = record.get("opendate")
        if not isinstance(label, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", label):
            raise ValueError("新浪日线原始日期无效")
        day = datetime.strptime(label, "%Y-%m-%d").date()
        if day > received.date():
            raise ValueError("新浪日线原始日期在未来")
        errors = []
        if day != received.date():
            errors.append("日线日期不是接收当日，不能据此断言本次成员同日")
        return {"available": True, "source_day": label, "source_as_of": None, "received_at": received.isoformat(),
                "net_inflow_yi": _converted(record.get("netamount"), "daily_net_inflow", errors, _YI),
                "r0_net_yi": _converted(record.get("r0_net"), "daily_r0_net", errors, _YI),
                "net_inflow_ratio_pct": _converted(record.get("ratioamount"), "daily_net_ratio", errors, Decimal("0.01")),
                "r0_ratio_pct": _converted(record.get("r0_ratio"), "daily_r0_ratio", errors, Decimal("0.01")),
                "degraded": ["opendate仅为原日线日期，不是源秒钟或成分生效时刻", *errors]}
    except Exception as exc:
        return {"available": False, "source_day": None, "source_as_of": None, "received_at": _clock(now).isoformat(),
                "net_inflow_yi": None, "r0_net_yi": None, "net_inflow_ratio_pct": None, "r0_ratio_pct": None,
                "degraded": ["新浪日线读取或校验失败", type(exc).__name__]}


def _totals(raw):
    result = {"total": len(raw)}
    for field, output, known in (("netamount", "net_inflow_yi", "net_known"), ("r0_net", "r0_net_yi", "r0_known")):
        numbers = [_number(row.get(field), field, []) for row in raw]
        valid = [number for number in numbers if number is not None]
        total = sum(valid, Decimal(0))
        amount = float(total / _YI) if len(valid) == len(raw) else None
        result[output] = amount if amount is not None and math.isfinite(amount) else None
        result[known] = len(valid)
    return result


def _match(a, b):
    if a is None or b is None:
        return "unknown"
    # Public amounts are displayed in yi; compare at the source's cent scale.
    return "matched" if abs(Decimal(str(a)) - Decimal(str(b))) <= Decimal("0.0000000001") else "mismatch"


async def fetch_members(http, kind, category, now, directory=None):
    """Complete same-source member coverage, with a separately dated day row."""
    fenlei, category, receipts = _kind(kind), _category(category), []
    _clock(now)
    async with asyncio.timeout(READ_SECONDS):
        official = await fetch_boards(http, kind, now) if directory is None else directory
        board = _directory_board(official, kind, category)
        bankuai = fenlei + "/" + category
        total = await _count(http, "MoneyFlow.ssc_bkzj_ssggzj", {"bankuai": bankuai}, now, receipts)
        raw = await _pages(http, "MoneyFlow.ssl_bkzj_ssggzj", {"sort": "symbol", "asc": "1", "bankuai": bankuai,
                           "shichang": ""}, total, now, receipts)
        rows = _member_rows(raw)
        if total != await _count(http, "MoneyFlow.ssc_bkzj_ssggzj", {"bankuai": bankuai}, now, receipts):
            raise ValueError("新浪成员计数在分页期间改变")
        daily = await _daily(http, bankuai, now, receipts)
    totals = _totals(raw)
    reconciliation = {"directory_net": _match(totals["net_inflow_yi"], board.get("net_inflow_yi")),
                      "daily_net": _match(totals["net_inflow_yi"], daily["net_inflow_yi"]),
                      "daily_r0": _match(totals["r0_net_yi"], daily["r0_net_yi"])}
    mismatches = (["成员与榜单/日线净额不一致，非原子同批/时点待核"]
                  if "mismatch" in reconciliation.values() else [])
    return {"available": True, "source": SOURCE, "kind": kind, "category": category, "name": board["name"],
            "source_as_of": None, "received_at": _clock(now).isoformat(),
            "coverage": {"total": total, "returned": len(rows), "complete": True}, "rows": rows,
            "totals": totals, "reconciliation": reconciliation, "daily_snapshot": daily,
            "degraded": [_CLOCK_UNKNOWN, "netamount为全部净额；r0_net为新浪主力口径，均非东财f62", *mismatches, *_reasons(rows)],
            "source_receipts": receipts}
