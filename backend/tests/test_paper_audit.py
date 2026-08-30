"""审计日志测试：reset 必须单行记录重置前状态与触发来源。

为什么不走 caplog：app.main 的 logging.basicConfig（TestClient 启动 lifespan 前
执行）会改变 root logger 状态，实测在"同进程先跑过一个起 TestClient 的测试"后，
后续测试的 caplog 捕获不到记录（pytest 日志插件与 basicConfig 的既有交互问题）。
审计断言对日志环境必须免疫，所以直接打桩模块级 logger。

辅助函数刻意不复用 test_paper_engine 的——跨测试文件导入会在全量运行时引入顺序耦合。
"""
from __future__ import annotations

import asyncio

import pytest

from app.schemas.market import Quote


class _FakeLog:
    """捕获 log.info 的桩；audit 行由 info 输出。"""

    def __init__(self):
        self.infos: list[str] = []
        self.warnings: list[str] = []

    def info(self, msg: str, *args):
        self.infos.append(msg % args if args else msg)

    def warning(self, msg: str, *args):
        self.warnings.append(msg % args if args else msg)


def _wipe_paper_tables():
    from app.core.db import get_engine, get_session_factory
    from app.models.paper import PaperAccount, PaperOrder, PaperPosition
    from app.models.watchlist import Base

    Base.metadata.create_all(get_engine())
    sf = get_session_factory()
    with sf() as db:
        for m in (PaperAccount, PaperOrder, PaperPosition):
            db.query(m).delete()
        db.commit()


def _make_engine(quote_map: dict):
    from app.core.db import get_session_factory
    from app.paper.engine import PaperTradingEngine

    async def quote_fn(symbol: str):
        return quote_map.get(symbol)

    return PaperTradingEngine(get_session_factory(), quote_fn)


@pytest.fixture()
def audit_lines(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    import app.paper.engine as eng_mod

    fake = _FakeLog()
    monkeypatch.setattr(eng_mod, "log", fake)
    return fake.infos


def test_reset_audit_contains_before_state(audit_lines: list[str]):
    _wipe_paper_tables()

    q = Quote(symbol="600519", price=100.0, limit_up_price=110.0, limit_down_price=90.0, source="t")
    engine = _make_engine({"600519": q})
    asyncio.run(engine.place_order("600519", "buy", 100.0, 200))

    engine.reset(source="api")

    lines = [x for x in audit_lines if "RESET audit" in x]
    assert lines, "缺少 RESET 审计日志"
    line = lines[-1]
    assert "source=api" in line
    assert (
        "before: positions=1 orders=1 cash=979995.0 initial=1000000.0" in line
    ), "必须完整记录重置前状态（持仓/委托/资金/初始额度）"
    assert "after: cash=1000000.0" in line
    assert "custom_initial=False" in line


def test_reset_audit_marks_custom_initial(audit_lines: list[str]):
    _wipe_paper_tables()
    engine = _make_engine({})
    engine.reset(initial_cash=500_000.0, source="api")

    lines = [x for x in audit_lines if "RESET audit" in x]
    assert lines and "custom_initial=True" in lines[-1]
    assert "initial=500000.0" in lines[-1]


def test_reset_audit_default_source_is_engine(audit_lines: list[str]):
    """直接调 engine（非 API 路径）时 source 标为 engine，与 API 路径可区分。"""
    _wipe_paper_tables()
    engine = _make_engine({})
    engine.reset()
    lines = [x for x in audit_lines if "RESET audit" in x]
    assert lines and "source=engine" in lines[-1]
