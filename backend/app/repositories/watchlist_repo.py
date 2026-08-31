from __future__ import annotations

from app.models.watchlist import WatchlistGroup, WatchlistItem
from app.core.db import utcnow

DEFAULT_WATCHLIST = ["600519", "000001", "300750", "601318"]
PROTECTED_GROUPS = {"默认"}  # 不允许重命名/删除（评审 A1：分组管理保护规则）


class WatchlistRepository:
    def __init__(self, session_factory):
        self._session_factory = session_factory

    def list_symbols(self) -> list[str]:
        with self._session_factory() as db:
            rows = db.query(WatchlistItem).order_by(WatchlistItem.id.asc()).all()
            return [r.symbol for r in rows]

    def list_items(self) -> list[WatchlistItem]:
        with self._session_factory() as db:
            return db.query(WatchlistItem).order_by(WatchlistItem.id.asc()).all()

    def add(self, symbol: str, name: str | None = None, note: str | None = None, group: str = "默认") -> WatchlistItem:
        symbol = symbol.strip()
        with self._session_factory() as db:
            existing = db.query(WatchlistItem).filter(WatchlistItem.symbol == symbol).one_or_none()
            if existing:
                return existing
            item = WatchlistItem(symbol=symbol, name=name, note=note, group_name=group or "默认", received_at=utcnow())
            db.add(item)
            db.commit()
            db.refresh(item)
            return item

    def update_group(self, symbol: str, group: str) -> bool:
        with self._session_factory() as db:
            item = db.query(WatchlistItem).filter(WatchlistItem.symbol == symbol).one_or_none()
            if not item:
                return False
            item.group_name = group or "默认"
            db.commit()
            return True

    def list_groups(self) -> list[str]:
        """分组清单 = 持久分组表 ∪ 成员派生（并集去重——两处都可能先出现）。"""
        with self._session_factory() as db:
            persisted = {r[0] for r in db.query(WatchlistGroup.name).all()}
            derived = {r[0] or "默认" for r in db.query(WatchlistItem.group_name).distinct().all()}
        return sorted(persisted | derived)

    def create_group(self, name: str) -> WatchlistGroup | None:
        """新建空分组；重名返回 None（由路由转 409）。"""
        name = (name or "").strip()
        if not name:
            return None
        with self._session_factory() as db:
            exists = db.query(WatchlistGroup).filter(WatchlistGroup.name == name).one_or_none()
            if exists or name in PROTECTED_GROUPS:
                return None
            row = WatchlistGroup(name=name)
            db.add(row)
            db.commit()
            db.refresh(row)
            return row

    def rename_group(self, old: str, new: str) -> str:
        """重命名分组并级联成员；返回 'ok' | 'missing' | 'conflict' | 'protected'。"""
        old = (old or "").strip()
        new = (new or "").strip()
        if not old or not new or old in PROTECTED_GROUPS:
            return "protected" if old in PROTECTED_GROUPS else "missing"
        with self._session_factory() as db:
            row = db.query(WatchlistGroup).filter(WatchlistGroup.name == old).one_or_none()
            derived_exists = (
                db.query(WatchlistItem).filter(WatchlistItem.group_name == old).count() > 0
            )
            if row is None and not derived_exists:
                return "missing"
            if new != old and (
                db.query(WatchlistGroup).filter(WatchlistGroup.name == new).count() > 0
                or db.query(WatchlistItem).filter(WatchlistItem.group_name == new).count() > 0
            ):
                return "conflict"
            # 级联成员（无论旧名是否在持久表）
            db.query(WatchlistItem).filter(WatchlistItem.group_name == old).update(
                {WatchlistItem.group_name: new}, synchronize_session=False
            )
            if row is not None:
                row.name = new
            db.commit()
        return "ok"

    def delete_group(self, name: str) -> str:
        """删除分组：成员回落「默认」；返回 'ok' | 'missing' | 'protected'。"""
        name = (name or "").strip()
        if not name or name in PROTECTED_GROUPS:
            return "protected" if name in PROTECTED_GROUPS else "missing"
        with self._session_factory() as db:
            row = db.query(WatchlistGroup).filter(WatchlistGroup.name == name).one_or_none()
            members = db.query(WatchlistItem).filter(WatchlistItem.group_name == name).count()
            if row is None and members == 0:
                return "missing"
            db.query(WatchlistItem).filter(WatchlistItem.group_name == name).update(
                {WatchlistItem.group_name: "默认"}, synchronize_session=False
            )
            if row is not None:
                db.delete(row)
            db.commit()
        return "ok"

    def remove(self, symbol: str) -> bool:
        with self._session_factory() as db:
            deleted = db.query(WatchlistItem).filter(WatchlistItem.symbol == symbol).delete()
            db.commit()
            return deleted > 0

    def ensure_seeded(self, defaults: list[str] | None = None) -> None:
        with self._session_factory() as db:
            if db.query(WatchlistItem).count() == 0:
                for sym in defaults or DEFAULT_WATCHLIST:
                    db.add(WatchlistItem(symbol=sym, source="default"))
                db.commit()
