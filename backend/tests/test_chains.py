"""传导链知识表测试（hotspot-pipeline-design §5 验收基准，2026-09-07）。

§5 四条固定测试集 = P0 完成的定义：标题取设计文档 §3 实测样例的忠实转述，
期望输出以 §5 表格为准。网络零依赖（match_chains/build_event 纯函数）。
"""

from __future__ import annotations

from datetime import date

from app.core.db import get_engine, get_session_factory
from app.events.chains import macro_calendar_note
from app.events.extract import build_event
from app.events.store import EventStore

import pytest


@pytest.fixture(autouse=True)
def _create_tables():
    from app.models.watchlist import Base as _B

    _B.metadata.create_all(get_engine())
    yield


def _rows(event: dict, target: str):
    return [d for d in event["directions"] if d["target"] == target]


def _by_type(event: dict, ttype: str):
    return [d for d in event["directions"] if d["target_type"] == ttype]


# --------------------------------------------------------------- §5 固定测试集

def test_s5_nfp_beat_expectations():
    """非农超预期 → market 偏空(弱) + 算力/果链观察关联。"""
    ev = build_event("美国8月非农大超预期 失业率意外回落")
    assert ev["category"] == "data"
    market = _by_type(ev, "market")
    assert len(market) == 1
    assert market[0]["target"] == "A股大盘"
    assert market[0]["direction"] == -1 and market[0]["strength"] == 1
    assert "nfp-ashare-validation" in market[0]["basis"]
    # 观察关联（direction=0，显式待判不猜方向）
    observe = {d["target"] for d in ev["directions"] if d["matched_by"] == "chain"}
    assert {"东数西算(算力)", "苹果概念"} <= observe
    assert all(_rows(ev, t)[0]["direction"] == 0 for t in ("东数西算(算力)", "苹果概念"))


def test_s5_el_nino_chain():
    """厄尔尼诺 → 种植/磷化工/化肥(2) + 电力/电网(1)，各带 chain 文本。"""
    ev = build_event("世界气象组织：厄尔尼诺强度创纪录 全球食品价格升至2022年来最高 化肥电力受益")
    expected = {
        "农业种植": (1, 2),
        "磷化工": (1, 2),
        "化肥": (1, 2),
        "绿色电力": (1, 1),
        "智能电网": (1, 1),
    }
    for target, (direction, strength) in expected.items():
        rows = _rows(ev, target)
        assert len(rows) == 1, f"{target} 应恰好一行"
        assert rows[0]["direction"] == direction and rows[0]["strength"] == strength
        assert rows[0]["chain"], f"{target} 必须带 chain 文本"
        assert rows[0]["matched_by"] == "chain"


def test_s5_ymtc_paper_proposed_demoted():
    """长存论文级 → 存储芯片(+1) 但 strength 降级为 1 + certainty=proposed。"""
    ev = build_event("长江存储3D NAND学术研究取得进展 实验室验证绕过EUV路线")
    assert ev["certainty"] == "proposed"
    rows = _rows(ev, "存储芯片")
    assert len(rows) == 1
    assert rows[0]["direction"] == 1
    assert rows[0]["strength"] == 1, "论文级事件不给高分（hotspot §3.3）"


def test_s5_gpt6_overseas_entity():
    """GPT-6 → AI应用/AI智能体(2) + 算力(1)（海外实体子表）。"""
    ev = build_event("OpenAI发布GPT-6 从问答走向直接做事")
    expected = {"AI应用": (1, 2), "AI智能体": (1, 2), "东数西算(算力)": (1, 1)}
    for target, (direction, strength) in expected.items():
        rows = _rows(ev, target)
        assert len(rows) == 1
        assert rows[0]["direction"] == direction and rows[0]["strength"] == strength


# --------------------------------------------------------------- 方向不猜 / 去重

def test_nfp_without_direction_words_is_zero():
    """非农标题无意外方向词 → market 行显式 0（三态，不猜）。"""
    ev = build_event("美国9月非农就业人口增加18.7万人")
    market = _by_type(ev, "market")
    assert market[0]["direction"] == 0
    assert "不猜" in market[0]["chain"]


def test_rate_cut_and_hike_weak_direction():
    cut = build_event("央行宣布降准0.5个百分点")
    assert _by_type(cut, "market")[0]["direction"] == 1
    hike = build_event("美联储暗示将继续加息")
    assert _by_type(hike, "market")[0]["direction"] == -1
    # 议息未决议 → 显式 0
    wait = build_event("本周FOMC议息会议召开在即")
    assert _by_type(wait, "market")[0]["direction"] == 0


def test_chain_vs_alias_semantic_dedupe():
    """alias「算力」⊂ 链行「东数西算(算力)」→ 语义重复只保留一行。"""
    ev = build_event("英伟达发布GPT-6级新品")
    algo_rows = [d for d in ev["directions"] if "算力" in d["target"]]
    assert len(algo_rows) == 1
    assert algo_rows[0]["matched_by"] == "alias", "先到的 alias 行优先"


def test_chain_rows_never_break_event_store_roundtrip():
    """含 market 行的事件经 EventStore 入库/读回完整（target_type 通用落库）。"""
    store = EventStore(get_session_factory())
    ev = build_event("美国8月非农大超预期 失业率意外回落")
    row, created = store.add_event(ev)
    assert created
    dirs = store.directions_of(row.id)
    assert any(d.target_type == "market" and d.target == "A股大盘" and d.direction == -1 for d in dirs)


# --------------------------------------------------------------- 宏观日历（G6）

def test_macro_calendar_nfp_day_and_next_trading_day():
    # 2026-09 第一个周五 = 09-04（美夏令时 → 20:30）
    note = macro_calendar_note(date(2026, 9, 4))
    assert note is not None and "20:30" in note and "非农" in note
    # 次一自然日（09-05 周六）与定价日周一 09-07 都给提示（定价日语义）
    assert macro_calendar_note(date(2026, 9, 7)) is not None
    # 平日 → None（显式缺失，不凑话）
    assert macro_calendar_note(date(2026, 9, 10)) is None


def test_macro_calendar_winter_time():
    # 2026-12 第一个周五 = 12-04（冬令时 → 21:30）
    note = macro_calendar_note(date(2026, 12, 4))
    assert note is not None and "21:30" in note
