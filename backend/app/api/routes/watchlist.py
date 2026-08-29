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
    group: str = Field(default="默认", max_length=32)


class GroupUpdate(BaseModel):
    group: str = Field(min_length=1, max_length=32)


def _serialize(item) -> dict:
    return {
        "symbol": item.symbol,
        "name": item.name,
        "note": item.note,
        "group_name": item.group_name,
        "source": item.source,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
    }


@router.get("/watchlist")
async def list_watchlist(repo: WatchlistRepository = Depends(get_watchlist_repository)) -> dict:
    return {"data": [_serialize(i) for i in repo.list_items()]}


@router.post("/watchlist", status_code=201)
async def add_watchlist(body: WatchlistAdd, repo: WatchlistRepository = Depends(get_watchlist_repository)) -> dict:
    item = repo.add(body.symbol, body.name, body.note, body.group)
    return {"data": _serialize(item)}


@router.get("/watchlist/groups")
async def list_groups(repo: WatchlistRepository = Depends(get_watchlist_repository)) -> dict:
    return {"data": repo.list_groups()}


@router.put("/watchlist/{symbol}/group")
async def update_group(symbol: str, body: GroupUpdate, repo: WatchlistRepository = Depends(get_watchlist_repository)) -> dict:
    if not repo.update_group(symbol, body.group):
        raise HTTPException(status_code=404, detail=f"{symbol} 不在自选中")
    return {"data": {"symbol": symbol, "group": body.group}}


@router.delete("/watchlist/{symbol}")
async def remove_watchlist(symbol: str, repo: WatchlistRepository = Depends(get_watchlist_repository)) -> dict:
    if not repo.remove(symbol):
        raise HTTPException(status_code=404, detail=f"{symbol} 不在自选中")
    return {"data": {"symbol": symbol, "removed": True}}
