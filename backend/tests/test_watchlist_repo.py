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


def test_watchlist_group_crud():
    repo = WatchlistRepository(get_session_factory())
    repo.remove("688041")
    repo.add("688041", "海光信息", None, "科技组")
    assert "科技组" in repo.list_groups()
    assert repo.update_group("688041", "半导体") is True
    groups = {i.symbol: i.group_name for i in repo.list_items()}
    assert groups["688041"] == "半导体"
    repo.update_group("688041", "默认")
    repo.remove("688041")
