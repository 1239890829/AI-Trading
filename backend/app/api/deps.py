from __future__ import annotations

from fastapi import Request

from app.repositories.watchlist_repo import WatchlistRepository
from app.services.quote_hub import QuoteHub


def get_hub(request: Request) -> QuoteHub:
    return request.app.state.hub


def get_watchlist_repository(request: Request) -> WatchlistRepository:
    return request.app.state.watchlist_repo
