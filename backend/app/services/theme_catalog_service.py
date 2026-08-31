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
from datetime import timedelta

import httpx
from sqlalchemy import func, select

from app.core.config import settings
from app.core.db import utcnow
from app.models.theme_catalog import Theme, ThemeMember, ThemeOverride

log = logging.getLogger(__name__)

CATALOG_TAG = "cn_concept"


# ---------------------------------------------------------------- 纯函数：解析


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

    # -- sync ------------------------------------------------------------

    async def sync_catalog(self) -> int:
        """全量 upsert 题材目录；返回目录条数。"""
        items = await self.fetch_catalog()
        with self._sf() as db:
            existing = {c for (c,) in db.execute(select(Theme.code)).all()}
            for it in items:
                if it["code"] in existing:
                    row = db.execute(select(Theme).where(Theme.code == it["code"])).scalar_one()
                    row.name = it["name"]
                    row.synced_at = utcnow()
                else:
                    db.add(Theme(code=it["code"], name=it["name"], source="ths_official"))
            db.commit()
        log.info("theme catalog synced: %d themes", len(items))
        return len(items)

    async def sync_members(self, code: str) -> int:
        """upsert 单题材成分（官方口径：当前成分，非历史成分）。"""
        items = await self.fetch_members(code)
        now = utcnow()
        with self._sf() as db:
            incoming_set = {it["symbol"] for it in items}
            current = set(
                db.execute(select(ThemeMember.symbol).where(ThemeMember.theme_code == code)).scalars()
            )
            # 官方成分是"当前"快照：消失的成分删除，保持与官方一致
            if current - incoming_set:
                from sqlalchemy import delete

                db.execute(
                    delete(ThemeMember).where(
                        ThemeMember.theme_code == code,
                        ThemeMember.symbol.in_(current - incoming_set),
                    )
                )
            for it in items:
                if it["symbol"] in current:
                    row = db.execute(
                        select(ThemeMember).where(
                            ThemeMember.theme_code == code, ThemeMember.symbol == it["symbol"]
                        )
                    ).scalar_one()
                    row.name = it["name"]
                    row.synced_at = now
                else:
                    db.add(ThemeMember(theme_code=code, symbol=it["symbol"], name=it["name"]))
            # 主题 synced_at 一并刷新，供 stale 判断
            theme = db.execute(select(Theme).where(Theme.code == code)).scalar_one_or_none()
            if theme is not None:
                theme.synced_at = now
            db.commit()
        log.info("theme members synced: %s -> %d stocks", code, len(items))
        return len(items)

    async def sync_stale_members(self, max_themes: int = 20, concurrency: int = 6) -> list[str]:
        """按 TTL 找出最久未同步的题材，限并发补齐。返回已同步代码。"""
        codes = self.stale_codes(max_themes)
        sem = asyncio.Semaphore(concurrency)

        async def _one(code: str) -> None:
            async with sem:
                try:
                    await self.sync_members(code)
                except Exception as exc:  # noqa: BLE001 单个题材失败不拖垮整批
                    log.warning("theme members sync failed %s: %s", code, exc)

        await asyncio.gather(*(_one(c) for c in codes))
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

    def catalog_size(self) -> int:
        with self._sf() as db:
            return int(db.execute(select(func.count(Theme.id))).scalar_one() or 0)
