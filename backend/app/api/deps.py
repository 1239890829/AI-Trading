from __future__ import annotations

from fastapi import HTTPException, Request

from app.core.config import settings
from app.repositories.watchlist_repo import WatchlistRepository
from app.services.quote_hub import QuoteHub


def normalize_symbol(symbol: str) -> str:
    """代码归一：去掉 .SH/.SZ 后缀，校验 6 位数字，非法即 400。

    S2-4：原为 `api/routes/theme_catalog.py` 的私有函数，却被
    `api/routes/events.py` 跨模块 import（`from app.api.routes.theme_catalog
    import _normalize_symbol`）。放在 `deps.py` 是刻意的——它抛 `HTTPException`，
    属 **HTTP 层**语义，不该下沉到 service（service 抛 HTTP 异常 = 层污染）。
    """
    sym = (symbol or "").strip().replace(".SH", "").replace(".SZ", "")
    if not sym.isdigit() or len(sym) != 6:
        raise HTTPException(status_code=400, detail=f"非法代码：{symbol!r}")
    return sym


def get_hub(request: Request) -> QuoteHub:
    return request.app.state.hub


def get_watchlist_repository(request: Request) -> WatchlistRepository:
    return request.app.state.watchlist_repo


async def require_write_token(request: Request) -> None:
    """写接口鉴权（技术评审 B6，opt-in 设计）：

    - 未配置 ``ASHARE_API_TOKEN``（本地开发默认）→ 全部放行，零影响；
    - 已配置 → 所有写请求必须携带 ``X-API-Token`` 头（或 ?token= 查询参数），
      面署到公网/NAS 时只需在 .env 配一个值即可获得写保护。
    """
    token = settings.api_token
    if not token:
        return
    provided = request.headers.get("X-API-Token") or request.query_params.get("token")
    if provided != token:
        raise HTTPException(status_code=401, detail="写操作需要 X-API-Token（服务端已启用鉴权）")
