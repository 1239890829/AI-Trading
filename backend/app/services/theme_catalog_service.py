"""题材字典与官方成分同步（linkage-design §3）。

数据源：同花顺官方 fuyao API（key 已在 settings.ths_api_key）：
- 目录 ``GET /api/a-share-index/catalog/ths-index-list?tag=cn_concept``（一次全量，实测 390 个）
- 成分 ``GET /api/a-share-index/constituents/ths-stock-list?thscode=88xxxx.TI``（单次一个代码，实测 0.2s）

设计纪律（延续项目范式）：
- **纯计算与 IO 分离**：parse_* 与 reconcile 是纯函数，可直接单测；
  fetch/sync 才碰网络与数据库。
- **不臆造**：目录/成分解析失败的部分跳过并计数，不用猜测值补位；
  全部失败抛错由路由转 502。
- 归属置信度分层见 app/models/theme_catalog.py 的 docstring。
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import func, select

from app.core.config import settings
from app.core.db import utcnow
from app.core.ttl_cache import TTLCache
from app.models.theme_catalog import Theme, ThemeMember, ThemeOverride

log = logging.getLogger(__name__)

CATALOG_TAG = "cn_concept"


# ---------------------------------------------------------------- 纯函数：解析


def parse_board_bars(payload: dict) -> list[dict]:
    """板块历史 K 线响应 → 按日期升序的 [{date, close, open, turnover, volume}]。

    官方字段：date_ms（毫秒）/ close_price / open_price / turnover（成交额）/ volume（股）。
    open/turnover/volume 为批次 D 回测补充解析（T+1 开盘买入收益、板块量比），
    缺失时为 None（老消费方只用 close，不受影响）。
    """
    items = ((payload or {}).get("data") or {}).get("item") or []
    bars: list[dict] = []
    for it in items:
        ts = it.get("date_ms")
        close = it.get("close_price")
        if not ts or close is None:
            continue
        bars.append({
            "date": datetime.fromtimestamp(ts / 1000, tz=timezone.utc).date().isoformat(),
            "close": float(close),
            "open": float(it["open_price"]) if it.get("open_price") is not None else None,
            "turnover": float(it["turnover"]) if it.get("turnover") is not None else None,
            "volume": float(it["volume"]) if it.get("volume") is not None else None,
        })
    bars.sort(key=lambda b: b["date"])
    return bars


def official_multi_day_changes(bars: list[dict]) -> dict:
    """官方板块 K 线 → 3/5/10 日涨跌幅（纯函数，linkage-design §3.5 T3）。

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
) -> dict:
    """热股榜 × 官方成分 → 题材级人气聚合（纯函数，B1 热股榜消费端）。

    归属口径 = 官方成分反查（linkage-design L3），不用关键词猜题材。
    - 题材热度 = 题材内热股 heat 合计；热股家数与榜内最高排名成员一并给出；
    - rank_change 沿用榜内最高排名成员的值——不单造「题材排名变化」指标，保持可解释；
    - 无官方归属的热股只留在 stocks 列表（附空 themes），不参与题材聚合（诚实口径）。

    :param stocks: provider.get_hot_stock_list 归一化行（rank/symbol/name/heat/rank_change/ts）
    :param official_by_symbol: symbol → [{theme_code, theme_name, source}]（get_official_for_symbol）
    """
    stock_rows: list[dict] = []
    theme_acc: dict[str, dict] = {}
    for s in stocks:
        officials = official_by_symbol.get(s["symbol"]) or []
        theme_names = [o["theme_name"] for o in officials]
        row = {
            "rank": s["rank"],
            "symbol": s["symbol"],
            "name": s.get("name"),
            "heat": s.get("heat"),
            "rank_change": s.get("rank_change"),
            "themes": theme_names,
        }
        stock_rows.append(row)
        for o in officials:
            acc = theme_acc.setdefault(
                o["theme_name"],
                {"theme": o["theme_name"], "theme_code": o.get("theme_code"),
                 "heat": 0.0, "hot_count": 0, "best": None},
            )
            acc["heat"] += s.get("heat") or 0.0
            acc["hot_count"] += 1
            if acc["best"] is None or s["rank"] < acc["best"]["rank"]:
                acc["best"] = {
                    "symbol": s["symbol"], "name": s.get("name"),
                    "rank": s["rank"], "rank_change": s.get("rank_change"),
                }
    themes = []
    for acc in theme_acc.values():
        best = acc.pop("best") or {}
        acc["best"] = best
        acc["basis"] = (
            f"{acc['hot_count']} 只官方成分热股人气合计；榜内最高"
            f" {best.get('name') or best.get('symbol')} 第 {best.get('rank')} 名"
        ) if best else ""
        themes.append(acc)
    themes.sort(key=lambda t: -t["heat"])
    return {
        "ts": next((s.get("ts") for s in stocks if s.get("ts")), None),
        "stocks": stock_rows,
        "themes": themes,
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
        await self._client.aclose()

    # -- fetch -----------------------------------------------------------

    async def fetch_catalog(self) -> list[dict]:
        r = await self._client.get(f"{self._base}/api/a-share-index/catalog/ths-index-list",
                                   params={"tag": CATALOG_TAG})
        r.raise_for_status()
        return parse_catalog_items(r.json())

    async def fetch_members(self, code: str) -> list[dict]:
        r = await self._client.get(f"{self._base}/api/a-share-index/constituents/ths-stock-list",
                                   params={"thscode": code})
        r.raise_for_status()
        return parse_member_items(r.json())

    async def fetch_board_bars(self, code: str, calendar_days: int = 35) -> list[dict]:
        """官方板块日 K（close 序列），供 3/5/10 日涨跌幅交叉验证（T3/B3）。"""
        end = int(time.time() * 1000)
        start = end - calendar_days * 86400_000
        r = await self._client.get(
            f"{self._base}/api/a-share-index/prices/historical",
            params={"thscode": code, "interval": "1d", "start": start, "end": end},
        )
        r.raise_for_status()
        return parse_board_bars(r.json())

    # -- sync ------------------------------------------------------------

    async def sync_catalog(self) -> int:
        """全量 upsert 题材目录；返回目录条数。

        ⚠️ 已有题材**名称未变化时不刷新 synced_at**：stale_codes 用 theme.synced_at
        判断成分是否过期，目录刷新若无条件更新时间戳，会把成分 TTL 判断永远"顶掉"
        ——2026-09-01 审计实锤：390 题材中 352 个（90%）成分从未同步、个股题材归属
        大面积缺失（永鼎股份查不到官方已标注的「光纤概念」）。
        DB 写入走 to_thread（评审 B2：同步 SQLite 不能跑在事件循环上）。
        """
        items = await self.fetch_catalog()
        await asyncio.to_thread(self._write_catalog, items)
        log.info("theme catalog synced: %d themes", len(items))
        return len(items)

    def _write_catalog(self, items: list[dict]) -> None:
        with self._sf() as db:
            # 批量拉全量再内存比对（评审 B2：原循环内逐条 SELECT 是 390 次 N+1）
            rows: dict[str, Theme] = {r.code: r for r in db.execute(select(Theme)).scalars()}
            for it in items:
                row = rows.get(it["code"])
                if row is not None:
                    if row.name != it["name"]:
                        row.name = it["name"]
                        row.synced_at = utcnow()
                else:
                    db.add(Theme(code=it["code"], name=it["name"], source="ths_official"))
            db.commit()

    async def sync_members(self, code: str) -> int:
        """upsert 单题材成分（官方口径：当前成分，非历史成分）。DB 写入走 to_thread。"""
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
        return len(items)

    async def sync_stale_members(self, max_themes: int = 20, concurrency: int = 6) -> list[str]:
        """按 TTL 找出最久未同步的题材，限并发补齐。返回已同步代码。"""
        codes = self.stale_codes(max_themes)
        sem = asyncio.Semaphore(concurrency)

        empty_after: list[str] = []

        async def _one(code: str) -> None:
            async with sem:
                try:
                    n = await self.sync_members(code)
                    if n == 0:
                        empty_after.append(code)
                except Exception as exc:  # noqa: BLE001 单个题材失败不拖垮整批
                    log.warning("theme members sync failed %s: %s", code, exc)
                    empty_after.append(code)

        await asyncio.gather(*(_one(c) for c in codes))
        # 空题材告警（校验规则）：成分为空 → 该题材下所有个股归属整体缺失。
        # 2026-09-01 审计实锤：352/390 题材成分从未同步（永鼎股份查不到官方已标的
        # 「光纤概念」）。同步后仍为空 = 官方题材确无成分（罕见）或拉取失败（重试）。
        if empty_after:
            log.warning(
                "theme members sync: %d/%d 个题材成分为空（官方无成分或拉取失败，下轮重试）: %s",
                len(empty_after), len(codes), empty_after[:10],
            )
        return codes

    def stale_codes(self, max_themes: int = 20) -> list[str]:
        """最久未同步（或从未同步成分）的题材代码，TTL 用 settings.theme_members_ttl_hours。"""
        cutoff = utcnow() - timedelta(hours=settings.theme_members_ttl_hours)
        with self._sf() as db:
            # 从未同步过成分的题材优先（member 表无行），其余按 synced_at 升序
            member_counts = dict(
                db.execute(
                    select(ThemeMember.theme_code, func.count(ThemeMember.id)).group_by(
                        ThemeMember.theme_code
                    )
                ).all()
            )
            rows = db.execute(select(Theme.code, Theme.synced_at).order_by(Theme.synced_at)).all()
        empty = [c for c, _ in rows if member_counts.get(c, 0) == 0]
        cutoff = cutoff.replace(tzinfo=None)  # SQLite 读回 naive datetime（UTC 语义），统一后再比
        stale = [c for c, ts in rows if member_counts.get(c, 0) > 0 and (ts is None or ts < cutoff)]
        return (empty + stale)[:max_themes]

    # -- query -----------------------------------------------------------

    def get_catalog(self, search: str | None = None, limit: int = 500) -> list[Theme]:
        with self._sf() as db:
            q = select(Theme).order_by(Theme.name)
            if search:
                q = q.where(Theme.name.contains(search))
            return list(db.execute(q.limit(limit)).scalars())

    def get_members(self, code: str) -> list[ThemeMember]:
        with self._sf() as db:
            return list(
                db.execute(
                    select(ThemeMember).where(ThemeMember.theme_code == code).order_by(ThemeMember.symbol)
                ).scalars()
            )

    def get_overrides(self, code: str | None = None) -> list[ThemeOverride]:
        with self._sf() as db:
            q = select(ThemeOverride)
            if code:
                q = q.where(ThemeOverride.theme_code == code)
            return list(db.execute(q).scalars())

    def get_active_overrides_for_symbol(self, symbol: str) -> list[ThemeOverride]:
        """该 symbol 的未过期人工纠错记录。"""
        now = utcnow()
        with self._sf() as db:
            rows = db.execute(select(ThemeOverride).where(ThemeOverride.symbol == symbol)).scalars()
            return [
                r for r in rows
                if r.expires_at is None or r.expires_at.replace(tzinfo=None) >= now.replace(tzinfo=None)
            ]

    def get_official_for_symbol(self, symbol: str, *, apply_manual: bool = True) -> list[dict]:
        """反查：该股票属于哪些官方题材（linkage-design §3.2 L3 层）。

        返回 [{theme_code, theme_name, source}]；默认叠加人工 override（exclude 剔除 /
        include 追加），来源徽标由前端按 source 渲染。
        """
        with self._sf() as db:
            rows = db.execute(
                select(ThemeMember, Theme.name)
                .join(Theme, Theme.code == ThemeMember.theme_code)
                .where(ThemeMember.symbol == symbol)
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

    def catalog_size(self) -> int:
        with self._sf() as db:
            return int(db.execute(select(func.count(Theme.id))).scalar_one() or 0)

    def member_symbols_bulk(self, codes: list[str]) -> dict[str, list[str]]:
        """批量查题材成分股列表（key=theme_code）。联动度计算用。"""
        if not codes:
            return {}
        with self._sf() as db:
            rows = db.execute(
                select(ThemeMember.theme_code, ThemeMember.symbol).where(ThemeMember.theme_code.in_(codes))
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
    - limit_up_count：涨停家数（change_pct ≥ 9.8 近似口径，20cm 板剔除）
    - top_gainers：涨幅前 3（name/symbol/change_pct）
    每项带 basis；成分无行情的按缺失计数（missing），不冒充 0。
    """
    out: dict[str, dict] = {}
    for code, members in theme_members.items():
        up = down = flat = missing = 0
        limit_up = 0
        total_amount = 0.0
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
            # 20cm 板（300/301/688/689 开头）阈值 19.8，其余 9.8
            is_20cm = sym.startswith(("300", "301", "688", "689"))
            if chg >= (19.8 if is_20cm else 9.8):
                limit_up += 1
            amount = q.get("amount")
            if amount is not None:
                total_amount += float(amount)
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
            "total_amount": round(total_amount, 0) if total_amount else 0.0,
            "top_gainers": gainers[:3],
            "basis": "；".join(basis_parts),
        }
    return out
