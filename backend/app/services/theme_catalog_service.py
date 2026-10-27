"""题材字典与官方成分同步（architecture-design §1）。

数据源：同花顺官方 fuyao API（key 已在 settings.ths_api_key）：
- 目录 ``GET /api/a-share-index/catalog/ths-index-list?tag=cn_concept``（一次全量，实测 390 个）
- 成分 ``GET /api/a-share-index/constituents/ths-stock-list?thscode=88xxxx.TI``（单次一个代码，实测 0.2s）

设计纪律（延续项目范式）：
- **纯计算与 IO 分离**：parse_* 与 reconcile 是纯函数，可直接单测；
  fetch/sync 才碰网络与数据库。
- **不臆造**：目录解析跳过缺字段项并计数；成分响应不通过写前校验则抛错，
  保留旧成员与同步时间；显式同步路由将失败转为 502。
- 归属置信度分层见 app/models/theme_catalog.py 的 docstring。
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
import logging
import math
import re
import time
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import func, select

from app.core.config import settings
from app.core.bjtime import BJ_TZ, beijing_now
from app.core.db import utcnow
from app.core.ttl_cache import TTLCache
from app.core.inflight_reads import close_inflight_reads, read_inflight
from app.models.theme_catalog import Theme, ThemeMember, ThemeOverride
from app.picks.pre_limit_radar import board_limit_pct, is_sealed

log = logging.getLogger(__name__)

CATALOG_TAG = "cn_concept"


def source_saved_clock(value: datetime | None) -> str | None:
    """SQLite stores these existing source-save clocks as naive UTC."""
    if value is None:
        return None
    return (value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)).isoformat()


def source_saved_state(clocks: list[datetime | None], hours: int) -> str:
    if not clocks or any(value is None for value in clocks):
        return "unknown"
    values = [datetime.fromisoformat(source_saved_clock(value)) for value in clocks]
    now = datetime.now(timezone.utc)
    if any(value > now for value in values):
        return "unknown"
    return "stale" if any(now - value > timedelta(hours=hours) for value in values) else "ready"


# ---------------------------------------------------------------- 纯函数：解析


def parse_board_bars(payload: dict) -> list[dict]:
    """板块历史 K 线响应 → 按日期升序的 [{date, close, open, turnover, volume}]。

    官方字段：date_ms（毫秒，北京交易日）/ close_price / open_price / turnover（成交额）/ volume（股）。
    open/turnover/volume 为批次 D 回测补充解析（T+1 开盘买入收益、板块量比），
    缺失时为 None（老消费方只用 close，不受影响）。
    """
    items = ((payload or {}).get("data") or {}).get("item") or []
    bars: list[dict] = []
    now = beijing_now()
    today = now.date()
    for it in items:
        ts = it.get("date_ms")
        close = it.get("close_price")
        if type(ts) is not int or ts <= 0 or ts > int(now.timestamp() * 1000) or close is None:
            continue
        try:
            day = datetime.fromtimestamp(ts / 1000, tz=BJ_TZ).date()
        except (OverflowError, OSError, ValueError):
            continue
        if day > today:
            continue
        bars.append({
            "date": day.isoformat(),
            "close": float(close),
            "open": float(it["open_price"]) if it.get("open_price") is not None else None,
            "turnover": float(it["turnover"]) if it.get("turnover") is not None else None,
            "volume": float(it["volume"]) if it.get("volume") is not None else None,
        })
    bars.sort(key=lambda b: b["date"])
    return bars


def official_multi_day_changes(bars: list[dict]) -> dict:
    """官方板块 K 线 → 3/5/10 日涨跌幅（纯函数，architecture-design §1 T3）。

    N 日涨幅 = close[-1] / close[-1-N] - 1（%）。交易日不足或除数为 0 → None，
    不用部分数据硬凑。
    """
    closes = [b["close"] for b in bars if b.get("close")]
    out: dict[str, float | None] = {}
    for n in (3, 5, 10):
        if len(closes) > n and closes[-1 - n]:
            out[f"chg_{n}d"] = round((closes[-1] / closes[-1 - n] - 1) * 100, 2)
        else:
            out[f"chg_{n}d"] = None
    return out


def parse_catalog_items(payload: dict) -> list[dict]:
    """目录响应 → [{code, name}]；缺字段的条目跳过并计数，不臆造。"""
    items = ((payload or {}).get("data") or {}).get("item") or []
    out: list[dict] = []
    skipped = 0
    for it in items:
        code = str(it.get("thscode") or "").strip()
        name = str(it.get("name") or "").strip()
        if not code or not name:
            skipped += 1
            continue
        out.append({"code": code, "name": name})
    if skipped:
        log.warning("题材目录解析跳过 %d 条缺字段条目", skipped)
    return out


def parse_member_items(payload: dict) -> list[dict]:
    """成分响应 → [{symbol, name}]；ticker 缺失跳过。"""
    items = ((payload or {}).get("data") or {}).get("item") or []
    out: list[dict] = []
    for it in items:
        ticker = str(it.get("ticker") or "").strip()
        name = str(it.get("name") or "").strip()
        if not ticker or not ticker.isdigit() or len(ticker) != 6:
            continue
        out.append({"symbol": ticker, "name": name})
    return out


def reconcile(
    attributions: list[tuple[str, str, list[str]]],
    members_by_theme: dict[str, set[str]],
    theme_names: dict[str, str],
) -> dict:
    """涨停归因 × 官方成分 三方校验（纯函数）。

    attributions: [(symbol, name, [题材名…])]，来自涨停池 reason 解析。
    members_by_theme: {题材名: {symbol…}}，官方成分按**名称**索引（归因串里是名称不是代码）。
    theme_names: {题材名: 题材代码}，原样附在报告里便于前端跳转。
    返回：归因冲突（有归因但非官方成分）、未知题材（归因题材不在官方目录）、
    统计计数。产出供 /api/themes/reconciliation 与人工复核队列使用。
    """
    conflicts: list[dict] = []
    unknown_themes: dict[str, int] = {}
    checked = 0

    for symbol, name, themes in attributions:
        for theme in themes:
            members = members_by_theme.get(theme)
            if members is None:
                # 官方目录里查无此题材：可能是目录外的新题材或归因错别字
                unknown_themes[theme] = unknown_themes.get(theme, 0) + 1
                continue
            checked += 1
            if symbol not in members:
                conflicts.append({"symbol": symbol, "name": name, "theme": theme})

    return {
        "checked_pairs": checked,
        "attribution_conflicts": conflicts,
        "unknown_themes": sorted(unknown_themes, key=lambda k: -unknown_themes[k]),
        "unknown_theme_counts": unknown_themes,
        "theme_names": theme_names,
    }


def aggregate_hot_themes(
    stocks: list[dict],
    official_by_symbol: dict[str, list[dict]],
    *,
    catalog_by_code: dict[str, dict],
    members_by_code: dict[str, list[str]],
    catalog_revision: int = 0,
    catalog_state: str = "unknown",
    membership_state: str = "ready",
) -> dict:
    """热股样本在正式成分中的集中度；不生成官方题材榜或选股资格。"""
    from math import isfinite

    stock_rows: list[dict] = []
    theme_acc: dict[tuple[str, str], dict] = {}
    seen_symbols = set()
    for s in stocks:
        symbol = s["symbol"]
        if symbol in seen_symbols:
            continue
        seen_symbols.add(symbol)
        officials = {}
        for item in official_by_symbol.get(symbol) or []:
            code, name = item.get("theme_code"), item.get("theme_name")
            registered = catalog_by_code.get(code) or {}
            if (item.get("source") == "ths_official" and registered.get("source") == "ths_official"
                    and name and registered.get("name") == name):
                officials[(code, name)] = item
        row = {
            "rank": s["rank"],
            "symbol": symbol,
            "name": s.get("name"),
            "heat": s.get("heat"),
            "rank_change": s.get("rank_change"),
            "themes": list(dict.fromkeys(name for _code, name in officials)),
        }
        stock_rows.append(row)
        for code, name in officials:
            acc = theme_acc.setdefault(
                (code, name),
                {"theme": name, "theme_code": code, "source": "ths_official",
                 "heat": 0.0, "heat_complete": True, "hot_count": 0, "hot_symbols": [], "best": None},
            )
            heat = s.get("heat")
            if isinstance(heat, (int, float)) and not isinstance(heat, bool) and isfinite(heat):
                acc["heat"] += heat
            else:
                acc["heat_complete"] = False
            acc["hot_count"] += 1
            acc["hot_symbols"].append(symbol)
            if acc["best"] is None or s["rank"] < acc["best"]["rank"]:
                acc["best"] = {
                    "symbol": symbol, "name": s.get("name"),
                    "rank": s["rank"], "rank_change": s.get("rank_change"),
                }
    themes = []
    for acc in theme_acc.values():
        code = acc["theme_code"]
        registered = catalog_by_code[code]
        members = set(members_by_code.get(code) or [])
        count_known = (catalog_state == "ready" and registered.get("members_current") is True
                       and bool(members) and set(acc["hot_symbols"]) <= members)
        acc["member_count"] = len(members) if count_known else None
        acc["member_as_of"] = registered.get("member_as_of")
        acc["concentration_pct"] = round(acc["hot_count"] / len(members) * 100, 2) if count_known else None
        if not acc["heat_complete"]:
            acc["heat"] = None
        best = acc["best"] or {}
        coverage = (f"{acc['hot_count']} / {acc['member_count']} 只已核成分，集中度 {acc['concentration_pct']:g}%"
                    if count_known else f"{acc['hot_count']} 只热股；当前完整成分数未核")
        acc["basis"] = (
            f"{coverage}；榜内最高"
            f" {best.get('name') or best.get('symbol')} 第 {best.get('rank')} 名"
        ) if best else ""
        themes.append(acc)
    themes.sort(key=lambda item: (item["concentration_pct"] is None,
                                  -(item["concentration_pct"] or 0), -item["hot_count"],
                                  item["theme_code"], item["theme"]))
    return {
        "ts": next((s.get("ts") for s in stocks if s.get("ts")), None),
        "stocks": stock_rows,
        "themes": themes,
        "ranking_basis": "hot_stock_concentration",
        "catalog_revision": catalog_revision,
        "catalog_state": catalog_state,
        "membership_state": membership_state,
        "scope": (f"本次读取 {len(stock_rows)} 只同花顺热股与已存正式目录的成分交集；"
                  "按热股占已核完整成分的比例排序，并非官方题材榜、全市场主线或入选资格。"
                  + ("目录未在有效期内，保留已存身份，当前成分分母未核。" if catalog_state != "ready" else "")
                  + ("归属读取失败，热股原榜仍保留，不能据空分类认定没有归属。" if membership_state != "ready" else "")),
    }


def apply_overrides(
    members: list[dict],
    overrides: list[ThemeOverride],
    theme_names: dict[str, str],
) -> list[dict]:
    """人工纠错裁决（纯函数）：exclude 从官方归属里剔除，include 追加。

    members: [{theme_code, theme_name, source}]；overrides 为该 symbol 的活跃记录。
    include 的题材若不在目录（theme_names 无此代码），以代码兜底命名并在 source 标 manual——
    不静默丢弃人工修正。
    """
    out = list(members)
    for ov in overrides:
        if ov.action == "exclude":
            out = [m for m in out if m["theme_code"] != ov.theme_code]
        elif ov.action == "include":
            if any(m["theme_code"] == ov.theme_code for m in out):
                continue
            out.append({
                "theme_code": ov.theme_code,
                "theme_name": theme_names.get(ov.theme_code, ov.theme_code),
                "source": "manual",
            })
    return out


def override_still_active(ov: ThemeOverride, now: datetime) -> bool:
    """人工纠错记录是否**仍在有效期内**（`expires_at` 为 None = 永久）。

    「单查」(`get_active_overrides_for_symbol`) 与「批量」(`_overrides_by_symbol`)
    **共用这一处判据**（2026-09-12 评审批次 3 / P-3②）——两处各写一遍同样的有效期
    比较，正是"同名不同口径"的滋生地：批量版一旦漏掉过期过滤，人工纠错就会**永久生效**，
    而且不会有任何报错。

    SQLite 读回的是 naive datetime（UTC 语义），两侧统一去 tzinfo 后再比。
    """
    if ov.expires_at is None:
        return True
    return ov.expires_at.replace(tzinfo=None) >= now.replace(tzinfo=None)


# ---------------------------------------------------------------- IO：同步与查询


class ThemeCatalogService:
    """题材目录/成分的拉取、落库与查询。"""

    def __init__(
        self,
        session_factory,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 15.0,
    ):
        self._sf = session_factory
        key = api_key if api_key is not None else settings.ths_api_key
        if not key:
            raise RuntimeError("theme catalog: ths_api_key missing")
        self._client = httpx.AsyncClient(
            timeout=timeout,
            trust_env=False,  # 项目纪律：行情类客户端不读系统代理
            headers={"X-api-key": key, "Accept": "application/json"},
        )
        self._base = (base_url or settings.ths_base_url).rstrip("/")
        # 板块日涨幅缓存（详情页题材 chips 排序/徽标用）：TTL 120s + 异步单飞 + 并发闸
        self._day_change_cache = TTLCache("theme_day_change", ttl=120.0, maxsize=512)
        self._board_fetch_sem = asyncio.Semaphore(6)
        # Scoped to this provider/client and its DB owner. Share only a running
        # sync; completed values never acquire another member freshness window.
        self._member_reads: dict[str, asyncio.Task[int]] = {}
        self._member_closing = False
        self._catalog_read: asyncio.Task | None = None
        self.revision = 0
        self._directory_reads = TTLCache("official_board_directory", ttl=86400, maxsize=2)
        self._snapshot_reads = TTLCache("official_board_snapshot", ttl=30, maxsize=32)
        self._industry_members = TTLCache("official_industry_members", ttl=60, maxsize=8)

    async def read_board_catalog(self, kind: str) -> dict:
        """One source identity owner; directory reads never wait for prices or funds."""
        if kind not in ("concept", "industry"):
            raise ValueError("unsupported official board kind")
        if kind == "concept":
            if not await asyncio.to_thread(self.catalog_size):
                await self.sync_catalog()

            def stored():
                rows = [row for row in self.get_catalog(limit=1000) if row.source == "ths_official"]
                items = [{"code": row.code, "name": row.name, "source": row.source,
                          "kind": kind, "synced_at": source_saved_clock(row.catalog_synced_at)}
                         for row in rows if row.source == "ths_official"]
                times = [row["synced_at"] for row in items]
                return {"items": items, "total": len(items), "kind": kind,
                        "catalog_state": source_saved_state([row.catalog_synced_at for row in rows], 24),
                        "source": "ths_official", "synced_at": min(times) if times and all(times) else None}

            return await asyncio.to_thread(stored)

        hit, value = self._directory_reads.get(kind)
        if hit:
            return deepcopy(value)

        async def fetch():
            hit, value = self._directory_reads.get(kind)
            if hit:
                return value
            response = await self._client.get(
                f"{self._base}/api/a-share-index/catalog/ths-index-list", params={"tag": "industry"})
            response.raise_for_status()
            payload = response.json()
            raw = (payload.get("data") or {}).get("item") if isinstance(payload, dict) else None
            items = parse_catalog_items(payload) if isinstance(raw, list) else []
            if (not isinstance(payload, dict) or type(payload.get("code")) is not int or payload["code"] != 0
                    or not raw or any(not isinstance(row, dict) or not isinstance(row.get("name"), str)
                                      or not row["name"].strip() or not isinstance(row.get("thscode"), str) for row in raw)
                    or len(items) != len(raw)
                    or len({row["code"] for row in items}) != len(items)
                    or any(not re.fullmatch(r"[0-9]{6}\.TI", row["code"]) for row in items)):
                raise RuntimeError("official industry catalog incomplete or failed")
            received = datetime.now(timezone.utc).isoformat()
            value = {"kind": kind, "source": "ths_official", "synced_at": received, "catalog_state": "ready",
                     "total": len(items), "items": [
                         {**row, "kind": kind, "source": "ths_official", "synced_at": received} for row in items]}
            self._directory_reads.set(kind, value)
            return value

        return await read_inflight(self, ("official-directory", kind), fetch)

    async def read_board_snapshots(self, codes: list[str]) -> dict:
        """Explicit, bounded same-source batch. Readiness is not the market clock."""
        wanted = tuple(sorted(set(codes)))
        if not wanted or len(wanted) > 50 or any(not re.fullmatch(r"[0-9]{6}\.TI", code) for code in wanted):
            raise ValueError("1 to 50 exact official board codes required")
        hit, value = self._snapshot_reads.get(wanted)
        if hit:
            return deepcopy(value)

        async def fetch():
            hit, value = self._snapshot_reads.get(wanted)
            if hit:
                return value
            received = None
            try:
                response = await self._client.get(
                    f"{self._base}/api/a-share-index/prices/snapshot", params={"thscodes": ",".join(wanted)})
                response.raise_for_status()
                payload = response.json()
                data = payload.get("data") if isinstance(payload, dict) else None
                raw = data.get("item") if isinstance(data, dict) else None
                if not isinstance(payload, dict) or type(payload.get("code")) is not int or payload["code"] != 0 or not isinstance(raw, list):
                    raise RuntimeError("official board snapshot failed")
                rows, seen = [], set()
                for row in raw:
                    code = row.get("thscode") if isinstance(row, dict) else None
                    if code not in wanted or code in seen:
                        raise RuntimeError("official board snapshot identity mismatch")
                    seen.add(code)
                    values = [row.get("last_price"), row.get("price_change_ratio_pct")]
                    if any(value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                                 or not math.isfinite(value)) for value in values):
                        raise RuntimeError("official board snapshot invalid number")
                    if values[0] is not None and values[0] <= 0:
                        values = [None, None]
                    rows.append({"code": code, "price": values[0], "change_pct": values[1]})
                stamp = data.get("timestamp")
                ready = (datetime.fromtimestamp(stamp / 1000, timezone.utc).isoformat()
                         if type(stamp) in (int, float) and 0 < stamp <= datetime.now(timezone.utc).timestamp() * 1000
                         and math.isfinite(stamp) else None)
                value = {"available": True, "source": "ths_official", "source_basis": "index_snapshot",
                         "source_as_of": None, "source_ready_at": ready,
                         "received_at": datetime.now(timezone.utc).isoformat(), "rows": rows,
                         "missing_codes": [code for code in wanted if code not in seen]}
            except Exception as exc:  # optional price failure cannot hide source identities
                log.warning("official board snapshot unavailable: %s", type(exc).__name__)
                value = {"available": False, "source": "ths_official", "source_basis": "index_snapshot",
                         "source_as_of": None, "source_ready_at": None,
                         "received_at": received or datetime.now(timezone.utc).isoformat(), "rows": [],
                         "missing_codes": list(wanted), "reason": "同花顺板块报价暂不可用"}
            self._snapshot_reads.set(wanted, value, ttl=30 if value["available"] else 5)
            return value

        return await read_inflight(self, ("official-board-snapshot", wanted), fetch)

    async def read_industry_members(self, code: str) -> list[dict]:
        hit, value = self._industry_members.get(code)
        if hit:
            return deepcopy(value)

        async def fetch():
            value = await self.fetch_members(code)
            self._industry_members.set(code, value)
            return value

        return await read_inflight(self, ("official-industry-members", code), fetch)

    async def day_changes(self, codes: list[str]) -> dict[str, float | None]:
        """官方板块指数当日涨跌幅（最新日 K close / 前一日 - 1，%）。

        用于详情页题材 chips（2026-09-01）：按涨跌幅排序 + 徽标展示，
        只显示最相关的少数题材，避免全部概念把分时/K线挤出可视区。
        单题材失败返回 None（best-effort，不拖垮整行 chips）。
        """
        out: dict[str, float | None] = {}

        async def one(code: str) -> None:
            async def load() -> float | None:
                # get_or_set 的 factory 是无参闭包；None 是合法值（新题材无样本），
                # cache_none=True 让它也进缓存，避免每次请求重复回源
                async with self._board_fetch_sem:
                    bars = await self.fetch_board_bars(code)
                closes = [b["close"] for b in bars if b.get("close")]
                if len(closes) < 2 or not closes[-2]:
                    return None
                return round((closes[-1] / closes[-2] - 1) * 100, 2)

            try:
                _, pct = await self._day_change_cache.get_or_set(code, load, cache_none=True)
                out[code] = pct
            except Exception as exc:  # noqa: BLE001 单题材失败不拖垮整批
                log.warning("theme day change failed %s: %s", code, exc)
                out[code] = None

        await asyncio.gather(*(one(c) for c in codes if c))
        return out

    async def aclose(self) -> None:
        self._member_closing = True
        cancelled = False
        try:
            await close_inflight_reads(self)
        except asyncio.CancelledError:
            cancelled = True  # shared source owners have actually drained
        # A canceled view may leave a shared source/write draining. Complete this
        # owner's bounded reads before closing the client they still consume.
        owned = ([self._catalog_read] if self._catalog_read and not self._catalog_read.done() else []) + list(self._member_reads.values())
        if owned:
            drained = asyncio.gather(*owned, return_exceptions=True)
            while not drained.done():
                try:
                    await asyncio.shield(drained)
                except asyncio.CancelledError:
                    cancelled = True
        closing = asyncio.create_task(self._client.aclose())
        while not closing.done():
            try:
                await asyncio.shield(closing)
            except asyncio.CancelledError:
                cancelled = True
        closing.result()
        if cancelled:
            raise asyncio.CancelledError

    # -- fetch -----------------------------------------------------------

    async def fetch_catalog(self) -> list[dict]:
        r = await self._client.get(f"{self._base}/api/a-share-index/catalog/ths-index-list",
                                   params={"tag": CATALOG_TAG})
        r.raise_for_status()
        payload = r.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        raw = data.get("item") if isinstance(data, dict) else None
        if (not isinstance(payload, dict) or type(payload.get("code")) is not int or payload["code"] != 0
                or not isinstance(raw, list) or not raw
                or type(data.get("timestamp")) is not int or data["timestamp"] <= 0
                or any(not isinstance(row, dict) for row in raw)):
            raise RuntimeError("theme catalog: incomplete or failed snapshot")
        items = parse_catalog_items(payload)
        codes = [row["code"] for row in items]
        if (len(items) != len(raw) or len(set(codes)) != len(codes)
                or any(not re.fullmatch(r"[0-9]{6}\.TI", code) for code in codes)):
            raise RuntimeError("theme catalog: invalid or duplicate identity")
        return items

    async def _fetch_member_payload(self, code: str) -> dict:
        """Validate the complete official snapshot before any consumer uses it."""
        r = await self._client.get(f"{self._base}/api/a-share-index/constituents/ths-stock-list",
                                   params={"thscode": code})
        r.raise_for_status()
        payload = r.json()
        if not isinstance(payload, dict) or type(payload.get("code")) is not int or payload["code"] != 0:
            raise RuntimeError("theme members: upstream business failure")
        data = payload.get("data")
        if (not isinstance(data, dict) or not isinstance(data.get("item"), list)
                or type(data.get("timestamp")) is not int or data["timestamp"] <= 0):
            raise RuntimeError("theme members: snapshot missing or not ready")
        if any(not isinstance(row, dict) for row in data["item"]):
            raise RuntimeError("theme members: malformed member")
        items = parse_member_items(payload)
        symbols = [row["symbol"] for row in items]
        if (len(items) != len(data["item"]) or len(set(symbols)) != len(symbols)
                or any(not symbol.isascii() for symbol in symbols)):
            raise RuntimeError("theme members: incomplete or duplicate members")
        return payload

    async def fetch_members(self, code: str) -> list[dict]:
        """只返回经校验可替换的响应；业务失败/损坏不能触发旧成员删除。"""
        return parse_member_items(await self._fetch_member_payload(code))

    async def fetch_index_members(self, code: str) -> dict:
        """Current standard-index membership; no quote reads or database writes."""
        payload = await self._fetch_member_payload(code)
        rows = payload["data"]["item"]
        if not rows:
            raise RuntimeError("index members: empty snapshot")
        items = []
        for row in rows:
            symbol, _, market = str(row.get("thscode") or "").partition(".")
            if symbol != row["ticker"] or market not in {"SH", "SZ", "BJ"} or not row.get("name"):
                raise RuntimeError("index members: security identity missing or mismatched")
            items.append({"symbol": symbol, "name": row["name"], "market": market})
        return {"items": items, "snapshot_at": datetime.fromtimestamp(
            payload["data"]["timestamp"] / 1000, tz=timezone.utc).isoformat()}

    async def fetch_board_bars(self, code: str, calendar_days: int = 35) -> list[dict]:
        """官方板块日 K（close 序列），供 3/5/10 日涨跌幅交叉验证（T3/B3）。"""
        end = int(time.time() * 1000)
        start = end - calendar_days * 86400_000
        r = await self._client.get(
            f"{self._base}/api/a-share-index/prices/historical",
            params={"thscode": code, "interval": "1d", "start": start, "end": end},
        )
        r.raise_for_status()
        try:
            payload = r.json()
        except ValueError as exc:
            raise RuntimeError("theme board bars: incomplete or failed snapshot") from exc
        data = payload.get("data") if isinstance(payload, dict) else None
        raw = data.get("item") if isinstance(data, dict) else None
        if (not isinstance(payload, dict) or type(payload.get("code")) is not int or payload["code"] != 0
                or not isinstance(raw, list) or any(not isinstance(row, dict) for row in raw)):
            raise RuntimeError("theme board bars: incomplete or failed snapshot")
        if (data.get("thscode") != code or data.get("interval") != "1d"
                or "adjust" not in data or data["adjust"] is not None):
            raise RuntimeError("theme board bars: identity or daily index contract mismatch")
        dates = [row.get("date_ms") for row in raw]
        if len(set(str(value) for value in dates)) != len(dates):
            raise RuntimeError("theme board bars: duplicate source date")
        source_dates = {}
        for row in raw:
            stamp = row.get("date_ms")
            if type(stamp) is not int or stamp <= 0:
                continue
            try:
                day = datetime.fromtimestamp(stamp / 1000, tz=BJ_TZ).date().isoformat()
            except (ValueError, OverflowError, OSError):
                continue
            if day in source_dates:
                raise RuntimeError("theme board bars: duplicate trading-day identity")
            source_dates[day] = stamp
            close = row.get("close_price")
            if close is not None and (isinstance(close, bool) or not isinstance(close, (int, float))
                                      or not math.isfinite(close) or close <= 0):
                raise RuntimeError("theme board bars: invalid close value")
        bars = parse_board_bars(payload)
        for bar in bars:
            bar.update(source="ths_official_kline", source_code=code,
                       source_date_ms=source_dates[bar["date"]])
        return bars

    # -- sync ------------------------------------------------------------

    def catalog_needs_sync(self) -> bool:
        """目录独立保鲜；成分同步不能推迟目录更新，未来时间也不算有效。"""
        now = utcnow().replace(tzinfo=None)
        with self._sf() as db:
            times = list(db.execute(select(Theme.catalog_synced_at).where(
                Theme.catalog_active.is_(True))).scalars())
        return not times or any(t is None or t > now or
                               now - t >= timedelta(hours=24) for t in times)

    async def ensure_catalog(self) -> None:
        if await asyncio.to_thread(self.catalog_needs_sync):
            await self.sync_catalog()

    async def sync_catalog(self) -> int:
        """合并并发读取；仅完整官方目录能原子更新当前资格，保留历史行。"""
        if self._member_closing:
            raise RuntimeError("theme catalog service is closing")
        task = self._catalog_read
        if task is None or task.done():
            async def sync():
                items = await self.fetch_catalog()
                await asyncio.to_thread(self._write_catalog, items)
                return len(items)
            task = asyncio.create_task(sync())
            self._catalog_read = task
            task.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
        return await asyncio.shield(task)

    def _write_catalog(self, items: list[dict]) -> None:
        if not items or len({r["code"] for r in items}) != len(items):
            raise ValueError("complete nonempty catalog required")
        now = utcnow()
        incoming = {r["code"] for r in items}
        with self._sf() as db:
            rows = {r.code: r for r in db.execute(select(Theme)).scalars()}
            for row in rows.values():
                if row.source == "ths_official":
                    row.catalog_active = row.code in incoming
                    row.catalog_synced_at = now
            for it in items:
                row = rows.get(it["code"])
                if row is None:
                    db.add(Theme(code=it["code"], name=it["name"], source="ths_official",
                                 catalog_active=True, catalog_synced_at=now))
                else:
                    old_member_time = row.synced_at
                    row.name = it["name"]
                    row.source = "ths_official"
                    row.catalog_active = True
                    row.catalog_synced_at = now
                    row.synced_at = old_member_time
            db.commit()
        self.revision = getattr(self, "revision", 0) + 1

    async def sync_members(self, code: str) -> int:
        """upsert 单题材成分（官方口径：当前成分，非历史成分）。DB 写入走 to_thread。"""
        if self._member_closing:
            raise RuntimeError("theme catalog service is closing")
        task = self._member_reads.get(code)
        if task is None or task.done():
            task = asyncio.create_task(self._sync_members_once(code))
            self._member_reads[code] = task

            def completed(done: asyncio.Task) -> None:
                if self._member_reads.get(code) is done:
                    self._member_reads.pop(code, None)
                if not done.cancelled():
                    done.exception()

            task.add_done_callback(completed)
        return await asyncio.shield(task)

    async def _sync_members_once(self, code: str) -> int:
        items = await self.fetch_members(code)
        n = await asyncio.to_thread(self._write_members, code, items)
        log.info("theme members synced: %s -> %d stocks", code, n)
        return n

    def _write_members(self, code: str, items: list[dict]) -> int:
        with self._sf() as db:
            incoming_set = {it["symbol"] for it in items}
            # 批量拉该题材全部成员行（评审 B2：原循环内逐条 SELECT）
            rows: dict[str, ThemeMember] = {
                r.symbol: r
                for r in db.execute(select(ThemeMember).where(ThemeMember.theme_code == code)).scalars()
            }
            current = set(rows)
            # 官方成分是"当前"快照：消失的成分删除，保持与官方一致
            if current - incoming_set:
                from sqlalchemy import delete

                db.execute(
                    delete(ThemeMember).where(
                        ThemeMember.theme_code == code,
                        ThemeMember.symbol.in_(current - incoming_set),
                    )
                )
            now = utcnow()
            for it in items:
                row = rows.get(it["symbol"])
                if row is not None:
                    row.name = it["name"]
                    row.synced_at = now
                else:
                    db.add(ThemeMember(theme_code=code, symbol=it["symbol"], name=it["name"]))
            # 主题 synced_at 一并刷新，供 stale 判断
            theme = db.execute(select(Theme).where(Theme.code == code)).scalar_one_or_none()
            if theme is not None:
                theme.synced_at = now
            db.commit()
        self.revision = getattr(self, "revision", 0) + 1
        return len(items)

    async def sync_stale_members(self, max_themes: int = 20, concurrency: int = 6) -> list[str]:
        """按 TTL 找出最久未同步的题材，限并发补齐。返回已同步代码。"""
        codes = self.stale_codes(max_themes)
        sem = asyncio.Semaphore(concurrency)

        empty_after: list[str] = []
        synced: set[str] = set()

        async def _one(code: str) -> None:
            async with sem:
                try:
                    n = await self.sync_members(code)
                    synced.add(code)
                    if n == 0:
                        empty_after.append(code)
                except Exception as exc:  # noqa: BLE001 单个题材失败不拖垮整批
                    log.warning("theme members sync failed %s: %s", code, exc)

        await asyncio.gather(*(_one(c) for c in codes))
        # 仅统计官方有效空快照；失败已单独告警，且保留旧成分与同步时间。
        if empty_after:
            log.warning(
                "theme members sync: %d/%d 个题材官方有效成分为空: %s",
                len(empty_after), len(codes), empty_after[:10],
            )
        return [code for code in codes if code in synced]

    def stale_codes(self, max_themes: int = 20) -> list[str]:
        """最久未同步（或从未同步成分）的题材代码，TTL 用 settings.theme_members_ttl_hours。"""
        now = utcnow().replace(tzinfo=None)
        cutoff = now - timedelta(hours=settings.theme_members_ttl_hours)
        with self._sf() as db:
            # 从未同步过成分的题材优先（member 表无行），其余按 synced_at 升序
            member_counts = dict(
                db.execute(
                    select(ThemeMember.theme_code, func.count(ThemeMember.id)).group_by(
                        ThemeMember.theme_code
                    )
                ).all()
            )
            rows = db.execute(select(Theme.code, Theme.synced_at).where(Theme.catalog_active.is_(True)).order_by(Theme.synced_at)).all()
        empty = [c for c, _ in rows if member_counts.get(c, 0) == 0]
        cutoff = cutoff.replace(tzinfo=None)  # SQLite 读回 naive datetime（UTC 语义），统一后再比
        stale = [c for c, ts in rows if member_counts.get(c, 0) > 0 and (ts is None or ts < cutoff or ts > now)]
        return (empty + stale)[:max_themes]

    # -- query -----------------------------------------------------------

    def get_catalog(self, search: str | None = None, limit: int = 500) -> list[Theme]:
        with self._sf() as db:
            q = select(Theme).where(Theme.catalog_active.is_(True)).order_by(Theme.name)
            if search:
                q = q.where(Theme.name.contains(search))
            return list(db.execute(q.limit(limit)).scalars())

    def get_members(self, code: str) -> list[ThemeMember]:
        with self._sf() as db:
            return list(
                db.execute(
                    select(ThemeMember).join(Theme, Theme.code == ThemeMember.theme_code).where(Theme.catalog_active.is_(True), Theme.synced_at <= utcnow(),
                        ThemeMember.synced_at <= utcnow(), ThemeMember.theme_code == code).order_by(ThemeMember.symbol)
                ).scalars()
            )

    def get_overrides(self, code: str | None = None) -> list[ThemeOverride]:
        with self._sf() as db:
            q = select(ThemeOverride)
            if code:
                q = q.where(ThemeOverride.theme_code == code)
            return list(db.execute(q).scalars())

    def get_active_overrides_for_symbol(self, symbol: str) -> list[ThemeOverride]:
        """该 symbol 的未过期人工纠错记录（单查；有效期判据见 `override_still_active`）。"""
        now = utcnow()
        with self._sf() as db:
            rows = db.execute(select(ThemeOverride).where(ThemeOverride.symbol == symbol)).scalars()
            return [r for r in rows if override_still_active(r, now)]

    def _overrides_by_symbol(self, symbols: list[str]) -> dict[str, list[ThemeOverride]]:
        """批量取多只股票的**未过期**人工纠错（key=symbol）。

        与 `get_active_overrides_for_symbol` 共用 `override_still_active` 判据
        （不是"照抄一遍同样的比较"），故两路的活跃集定义恒等。
        """
        if not symbols:
            return {}
        now = utcnow()
        with self._sf() as db:
            rows = db.execute(
                select(ThemeOverride).where(ThemeOverride.symbol.in_(symbols))
            ).scalars()
            out: dict[str, list[ThemeOverride]] = {}
            for r in rows:
                if override_still_active(r, now):
                    out.setdefault(r.symbol, []).append(r)
            return out

    def get_official_for_symbol(self, symbol: str, *, apply_manual: bool = True) -> list[dict]:
        """反查：该股票属于哪些官方题材（architecture-design §1 L3 层）。

        返回 [{theme_code, theme_name, source}]；默认叠加人工 override（exclude 剔除 /
        include 追加），来源徽标由前端按 source 渲染。
        """
        with self._sf() as db:
            rows = db.execute(
                select(ThemeMember, Theme.name)
                .join(Theme, Theme.code == ThemeMember.theme_code)
                .where(Theme.catalog_active.is_(True), Theme.synced_at <= utcnow(),
                       ThemeMember.synced_at <= utcnow(), ThemeMember.symbol == symbol)
                .order_by(ThemeMember.theme_code)
            ).all()
        members = [
            {"theme_code": m.theme_code, "theme_name": name, "source": m.attribution_source}
            for m, name in rows
        ]
        if not apply_manual:
            return members
        overrides = self.get_active_overrides_for_symbol(symbol)
        if not overrides:
            return members
        return apply_overrides(members, overrides, {})

    def official_for_symbols_bulk(
        self, symbols: list[str], *, apply_manual: bool = True
    ) -> dict[str, list[dict]]:
        """批量反查：多只股票各自属于哪些官方题材（key=symbol）。

        与逐只 `get_official_for_symbol` **行为等价**（2026-09-12 评审批次 3 / P-3②），
        等价性由 `test_theme_catalog.py::test_official_for_symbols_bulk_matches_single_read`
        直接 A/B 对照钉住（含 override 生效与 override 过期两种情形）：
        · 同一张表、同一 join、同样按 `ThemeMember.theme_code` 排序 ⇒ 每只内部顺序一致；
        · 默认同样叠加人工 override，且活跃集判据共用 `override_still_active`；
        · **未命中的 symbol 返回空列表**（不是缺键），调用方不必再 `.get(sym) or []`。

        实现上把 `N 只 × (1 次成员 + 1 次 override)` 压成 **2 次**查询：成员与
        override 各一次 `in_`。原逐只版是评审批次 3 的 P-3② 热点——24 只候选 ≈ 48 次
        **同步** SQLite 查询落在 async 函数里，既浪费又阻塞事件循环（与 P-2 同族）。
        """
        uniq = list(dict.fromkeys(symbols))
        if not uniq:
            return {}
        with self._sf() as db:
            rows = db.execute(
                select(ThemeMember.symbol, ThemeMember, Theme.name)
                .join(Theme, Theme.code == ThemeMember.theme_code)
                .where(Theme.catalog_active.is_(True), Theme.synced_at <= utcnow(),
                       ThemeMember.synced_at <= utcnow(), ThemeMember.symbol.in_(uniq))
                .order_by(ThemeMember.symbol, ThemeMember.theme_code)
            ).all()
        grouped: dict[str, list[dict]] = {s: [] for s in uniq}
        for sym, m, name in rows:
            grouped[sym].append(
                {"theme_code": m.theme_code, "theme_name": name, "source": m.attribution_source}
            )
        if not apply_manual:
            return grouped
        overrides = self._overrides_by_symbol(uniq)
        if not overrides:
            return grouped
        return {s: apply_overrides(v, overrides.get(s) or [], {}) for s, v in grouped.items()}

    def catalog_size(self) -> int:
        with self._sf() as db:
            return int(db.execute(select(func.count(Theme.id)).where(Theme.catalog_active.is_(True))).scalar_one() or 0)

    def member_symbols_bulk(self, codes: list[str]) -> dict[str, list[str]]:
        """批量查题材成分股列表（key=theme_code）。联动度计算 / 题材合力用。

        2026-09-11（P0-2）显式 `order_by(theme_code, symbol)`：调用方会做 `[:N]` 截断
        （题材合力取前 200 只），不排序时取到的是 DB 扫描顺序（≈rowid 插入序），
        与单条 `get_members`（按 symbol 排序）不一致，且结果随插入顺序漂移。
        排序后与 `get_members` 同序，批量替换单查才是**行为等价**的。
        """
        if not codes:
            return {}
        with self._sf() as db:
            rows = db.execute(
                select(ThemeMember.theme_code, ThemeMember.symbol)
                .join(Theme, Theme.code == ThemeMember.theme_code)
                .where(Theme.catalog_active.is_(True), Theme.synced_at <= utcnow(), ThemeMember.synced_at <= utcnow())
                .where(ThemeMember.theme_code.in_(codes))
                .order_by(ThemeMember.theme_code, ThemeMember.symbol)
            ).all()
        out: dict[str, list[str]] = {}
        for code, sym in rows:
            out.setdefault(code, []).append(sym)
        return out


def direction_alignment(
    members: list[str],
    chg_by_symbol: dict[str, float | None],
    market_chg: float | None,
    *,
    min_n: int = 5,
) -> float | None:
    """题材与今日整体涨跌行情的联动度（纯函数，2026-09-01 用户反馈 #2）。

    定义：方向一致家数占比——大盘涨时取成分股上涨家数占比、大盘跌时取下跌家数
    占比。衡量「该题材今天跟不跟大盘走」：越接近 1 表示成分与大盘同向越整齐
    （题材就是今天的行情主线），**排序依据只用方向不依赖涨跌幅数值大小**。

    大盘平盘（方向无意义）或有效成分不足 min_n 时返回 None（不臆造）。
    """
    if market_chg is None or market_chg == 0:
        return None
    n_up = sum(1 for s in members if (chg_by_symbol.get(s) or 0) > 0)
    n_down = sum(1 for s in members if (chg_by_symbol.get(s) or 0) < 0)
    n = n_up + n_down
    if n < min_n:
        return None
    same = n_up if market_chg > 0 else n_down
    return round(same / n, 3)


def aggregate_theme_strength(
    theme_members: dict[str, list[str]],
    quotes: dict[str, dict],
) -> dict[str, dict]:
    """题材内资金合力聚合（纯函数，P1-5）。

    归属口径 = 官方成分（theme_member 反查），不用关键词猜。
    输入行情为腾讯批量快照（与个股行情同源）的归一化行：
    {symbol: {change_pct, amount, price, name, last_price}}。

    合力指标（可解释、不臆造）：
    - up/down/flat：涨/跌/平家数（涨跌家数比是最直观的资金合力证据）
    - avg_change_pct：成分等权平均涨幅（少数大票不会绑架题材观感）
    - total_amount：板块成交额合计（元）
    - limit_up_count：涨停家数（按板块的单点封板判定 `is_sealed`：主板 ≥9.7 /
      创业科创 ≥19.7 / 北交所 ≥29.7；**不再用「≥9.8、20cm 板剔除」的近似口径**）
    - top_gainers：涨幅前 3（name/symbol/change_pct）
    每项带 basis；成分无行情的按缺失计数（missing），不冒充 0。
    """
    out: dict[str, dict] = {}
    for code, members in theme_members.items():
        up = down = flat = missing = 0
        limit_up = 0
        total_amount = 0.0
        amount_count = 0
        chg_sum = 0.0
        chg_n = 0
        gainers: list[dict] = []
        for sym in members:
            q = quotes.get(sym)
            if q is None or q.get("change_pct") is None:
                missing += 1
                continue
            chg = float(q["change_pct"])
            chg_sum += chg
            chg_n += 1
            if chg > 0.005:
                up += 1
            elif chg < -0.005:
                down += 1
            else:
                flat += 1
            # 封板口径**单点**（2026-09-14 收口）：此前这里是第三份独立硬编码实现
            # ——`19.8 if is_20cm else 9.8`，只有两档，**遗漏北交所 30% 板**
            # （83/87/92 段的 +9.9% 会被误计为涨停）。现与题材路由、临板雷达同源。
            if is_sealed(chg, board_limit_pct(sym, str(q.get("name") or ""))):
                limit_up += 1
            amount = q.get("amount")
            if amount is not None:
                total_amount += float(amount)
                amount_count += 1
            gainers.append({"symbol": sym, "name": q.get("name"), "change_pct": chg})
        gainers.sort(key=lambda g: -(g["change_pct"] or 0))
        basis_parts = [f"{up}涨/{down}跌/{flat}平"]
        if missing:
            basis_parts.append(f"{missing} 只无行情")
        if chg_n:
            basis_parts.append(f"等权平均 {round(chg_sum / chg_n, 2)}%")
        out[code] = {
            "count": len(members),
            "up": up,
            "down": down,
            "flat": flat,
            "missing": missing,
            "limit_up_count": limit_up,
            "avg_change_pct": round(chg_sum / chg_n, 2) if chg_n else None,
            "total_amount": round(total_amount, 0) if amount_count else None,
            "amount_count": amount_count,
            "top_gainers": gainers[:3],
            "basis": "；".join(basis_parts),
        }
    return out
