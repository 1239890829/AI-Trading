from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import get_watchlist_repository
from app.repositories.watchlist_repo import WatchlistRepository

router = APIRouter(tags=["watchlist"])


class WatchlistAdd(BaseModel):
    symbol: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")
    name: str | None = None
    note: str | None = None


def _serialize(item) -> dict:
    return {
        "symbol": item.symbol,
        "name": item.name,
        "note": item.note,
        "source": item.source,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
    }


@router.get("/watchlist")
async def list_watchlist(repo: WatchlistRepository = Depends(get_watchlist_repository)) -> dict:
    return {"data": [_serialize(i) for i in repo.list_items()]}


@router.post("/watchlist", status_code=201)
async def add_watchlist(body: WatchlistAdd, repo: WatchlistRepository = Depends(get_watchlist_repository)) -> dict:
    item = repo.add(body.symbol, body.name, body.note)
    return {"data": _serialize(item)}


@router.delete("/watchlist/{symbol}")
async def remove_watchlist(symbol: str, repo: WatchlistRepository = Depends(get_watchlist_repository)) -> dict:
    if not repo.remove(symbol):
        raise HTTPException(status_code=404, detail=f"{symbol} 不在自选中")
    return {"data": {"symbol": symbol, "removed": True}}
