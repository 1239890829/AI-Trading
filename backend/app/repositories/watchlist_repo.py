from __future__ import annotations

from app.models.watchlist import WatchlistItem
from app.core.db import utcnow

DEFAULT_WATCHLIST = ["600519", "000001", "300750", "601318"]


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
        with self._session_factory() as db:
            rows = db.query(WatchlistItem.group_name).distinct().order_by(WatchlistItem.group_name).all()
            return [r[0] or "默认" for r in rows]

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
