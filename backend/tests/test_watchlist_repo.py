from __future__ import annotations

from app.core.db import get_session_factory
from app.repositories.watchlist_repo import WatchlistRepository


def test_watchlist_repo_crud():
    repo = WatchlistRepository(get_session_factory())
    repo.remove("688041")  # 清理可能的遗留
    assert repo.add("688041", "海光信息") is not None
    assert "688041" in repo.list_symbols()
    again = repo.add("688041")
    assert again.id is not None  # 幂等：不会产生重复行
    assert repo.remove("688041") is True
    assert repo.remove("688041") is False
