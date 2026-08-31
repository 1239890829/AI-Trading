from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import get_watchlist_repository, require_write_token
from app.repositories.watchlist_repo import WatchlistRepository

router = APIRouter(tags=["watchlist"])


class WatchlistAdd(BaseModel):
    symbol: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")
    name: str | None = None
    note: str | None = None
    group: str = Field(default="默认", max_length=32)


class GroupUpdate(BaseModel):
    group: str = Field(min_length=1, max_length=32)


class GroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=32)


class GroupRename(BaseModel):
    new_name: str = Field(min_length=1, max_length=32)


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


@router.post("/watchlist", status_code=201, dependencies=[Depends(require_write_token)])
async def add_watchlist(body: WatchlistAdd, repo: WatchlistRepository = Depends(get_watchlist_repository)) -> dict:
    item = repo.add(body.symbol, body.name, body.note, body.group)
    return {"data": _serialize(item)}


@router.get("/watchlist/groups")
async def list_groups(repo: WatchlistRepository = Depends(get_watchlist_repository)) -> dict:
    return {"data": repo.list_groups()}


@router.post("/watchlist/groups", status_code=201, dependencies=[Depends(require_write_token)])
async def create_group(body: GroupCreate, repo: WatchlistRepository = Depends(get_watchlist_repository)) -> dict:
    row = repo.create_group(body.name)
    if row is None:
        raise HTTPException(status_code=409, detail=f"分组「{body.name}」已存在或名称无效")
    return {"data": {"name": row.name}}


@router.put("/watchlist/groups/{name}", dependencies=[Depends(require_write_token)])
async def rename_group(name: str, body: GroupRename, repo: WatchlistRepository = Depends(get_watchlist_repository)) -> dict:
    result = repo.rename_group(name, body.new_name)
    if result == "protected":
        raise HTTPException(status_code=400, detail=f"分组「{name}」是保留分组，不可重命名")
    if result == "missing":
        raise HTTPException(status_code=404, detail=f"分组「{name}」不存在")
    if result == "conflict":
        raise HTTPException(status_code=409, detail=f"分组「{body.new_name}」已存在")
    return {"data": {"old": name, "new": body.new_name}}


@router.delete("/watchlist/groups/{name}", dependencies=[Depends(require_write_token)])
async def delete_group(name: str, repo: WatchlistRepository = Depends(get_watchlist_repository)) -> dict:
    result = repo.delete_group(name)
    if result == "protected":
        raise HTTPException(status_code=400, detail=f"分组「{name}」是保留分组，不可删除")
    if result == "missing":
        raise HTTPException(status_code=404, detail=f"分组「{name}」不存在")
    return {"data": {"name": name, "deleted": True, "members_moved_to": "默认"}}


@router.put("/watchlist/{symbol}/group", dependencies=[Depends(require_write_token)])
async def update_group(symbol: str, body: GroupUpdate, repo: WatchlistRepository = Depends(get_watchlist_repository)) -> dict:
    if not repo.update_group(symbol, body.group):
        raise HTTPException(status_code=404, detail=f"{symbol} 不在自选中")
    return {"data": {"symbol": symbol, "group": body.group}}


@router.delete("/watchlist/{symbol}", dependencies=[Depends(require_write_token)])
async def remove_watchlist(symbol: str, repo: WatchlistRepository = Depends(get_watchlist_repository)) -> dict:
    if not repo.remove(symbol):
        raise HTTPException(status_code=404, detail=f"{symbol} 不在自选中")
    return {"data": {"symbol": symbol, "removed": True}}
