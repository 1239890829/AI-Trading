"""`BUG-016` 子项③：通知空态可解释性（`app/picks/notification_diagnostics.py`）。

## 本文件要守住的是什么

用户现场提问「为什么消息通知一个也没有呢」暴露的**不是"没有机会"，而是"空响应体
不可解释"**：三种完全不同的处境在旧契约里**同形**——
真无机会（跑了但全被否）／链路未跑／上游空（盘前没选出）。

因此本文件的判据围绕**两件事**展开，其余都是次要的：

1. **三态必须互不相同**（同一份输入不可能同时是两种态）——
   这是本项存在的唯一理由，所以它是第一条用例；
2. **不许把「无证据」写成「证据表明没有」**（`BUG-016` 轮实测踩过的同类错）：
   读库失败必须是 `unavailable`，**不得**退化成"没有机会"。

## 计数口径守卫（易错点，单列）

盘中每 60s 一拍，同一只票会被否决几十次（真库实测：`polls=16` / 唯一 symbol **1**）。
`by_decision` 与 `reject_reasons` **按 symbol 去重**、`polls` 单列 ——
按记录数统计会把「1 只票」读成「16 次问题」。
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.daily_pick import DailyPickSet
from app.models.opportunity_learning import OpportunityDecisionSnapshot
from app.models.watchlist import Base
from app.picks.notification_diagnostics import (
    ELIGIBLE_DECISIONS,
    TIER_ORDER,
    notification_diagnostics,
)

DAY = "2026-09-16"


def _factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'notif-diag.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed_pick_set(sf, *, items: list[dict], gate: dict | None = None) -> None:
    with sf() as db:
        db.add(DailyPickSet(
            date=DAY,
            items=json.dumps(items),
            meta=json.dumps({"gate": gate or {"stand_aside": False, "level": "none"}}),
        ))
        db.commit()


def _seed_snapshots(sf, *, symbol: str, decision: str, tier: str | None,
                    reason: str | None, polls: int = 1, step_minutes: int = 1) -> None:
    """写入 `polls` 拍同一只票的 notification 记录（**用于验证去重口径**）。

    ⚠️ `run_id` 必须全局唯一：表有 `UniqueConstraint(run_id, stage, symbol, source_theme)`
    —— 同一 symbol 写多拍（或同一只票先拒后过）时若 `run_id` 重复，**夹具会先崩**，
    而不是实现出错。这里的计数器正是为此（首版漏了它，实测撞约束）。
    """
    with sf() as db:
        for i in range(polls):
            _RUN_SEQ[0] += 1
            db.add(OpportunityDecisionSnapshot(
                snapshot_id=f"{symbol}-{decision}-{_RUN_SEQ[0]}",
                run_id=f"run-{_RUN_SEQ[0]}", trade_date=DAY,
                as_of=datetime(2026, 9, 16, 13, i * step_minutes),
                scenario="buy_point", stage="notification", symbol=symbol,
                name="测试股", source_theme="", decision=decision, rank=None,
                strategy_version="v", feature_version="v", data_state="ready",
                entry_price=None,
                evidence=json.dumps({
                    "gate_decision": "passed" if decision in ELIGIBLE_DECISIONS else "rejected",
                    "gate_reason": reason, "confidence_tier": tier,
                    "vetoes": [], "observation_only": True, "buy_range": None,
                }),
            ))
        db.commit()


#: 跨用例共享的自增序号（见 `_seed_snapshots` 的唯一约束说明）。
_RUN_SEQ: list[int] = [0]


def _cand(symbol: str = "603162", *, tier: str = "observe", score: float = 50.6,
          observation_only: bool = True, buy_range=None) -> dict:
    return {
        "symbol": symbol, "name": "测试股", "score": score,
        "confidence": {"tier": tier}, "observation_only": observation_only,
        "buy_range": buy_range, "vetoes": [],
    }


# ---------------------------------------------------------------- 1. 核心：三态可区分

def test_pipeline_not_run_and_ran_rejected_are_distinguishable(tmp_path):
    """**本项存在的理由**：同一份"空通知"下，「链路未跑」与「跑了但全被否」必须可区分。

    旧契约里两者都是 `{"items": [], "count": 0}` —— 这正是用户问「为什么一个也没有」
    却得不到答案的原因。本用例把两条路径**并排**断言，防止将来有人把两态合并
    （合并后每条单独断言都可能仍然通过，只有并排比才抓得住）。
    """
    sf = _factory(tmp_path)
    _seed_pick_set(sf, items=[_cand()])                     # 有候选，但无判定记录

    not_run = notification_diagnostics(DAY, session_factory=sf)

    _seed_snapshots(sf, symbol="603162", decision="rejected", tier="observe",
                    reason="置信档 observe 不足（需 executable/strong）")
    ran_rejected = notification_diagnostics(DAY, session_factory=sf)

    assert not_run["state"] == "no_run"
    assert ran_rejected["state"] == "ran_rejected"
    assert not_run["state"] != ran_rejected["state"], "两态被合并 ⇒ 用户又回到「无法区分」"
    # 两条 note 必须指向**不同的下一步动作**（否则区分没有实用价值）
    assert "未跑到" in not_run["note"] and "已跑" in ran_rejected["note"]


def test_ran_rejected_note_declares_reasons_are_latest_poll_only(tmp_path):
    """`reasons` 的两条**读法纪律**必须写在 note 里（否则会被读成全天/完整原因）。

    真库实测撞到（2026-09-16 13:15 → 13:21，同一只票）：

    - 13:15 读是「置信档 observe 不足」；13:21 已变成「快照无现价（不臆造）」
      ⇒ 只给最新原因而不声明，用户会把它读成「全天主因」；
    - 而 `buy_point.evaluate_buy_points` **按门顺序短路**（price → tier → veto →
      闸门 → 买区 → 涨停区）：`快照无现价` 时**档位门根本没被评估**
      ⇒ 「原因没提档位」**不等于**「档位已通过」。
      这两条恰好都指向 `BUG-016` **待拍板的档位门**，误读一次就会拍错方向。
    """
    sf = _factory(tmp_path)
    _seed_pick_set(sf, items=[_cand()])
    _seed_snapshots(sf, symbol="603162", decision="rejected", tier="observe",
                    reason="快照无现价（不臆造）")

    got = notification_diagnostics(DAY, session_factory=sf)
    assert got["state"] == "ran_rejected"
    note = got["note"]
    # 纪律①：原因只取最新一拍
    assert "最新一拍" in note and "不是全天分布" in note
    # 纪律②：原因只记首个未过的门（短路）⇒ 不得让人以为"没提档位就是档位没问题"
    assert "首个未过的门" in note and "不等于「档位已通过」" in note

    # 反向断言：口径声明不得被换成看起来更顺、但会让人误读的说法
    assert "主要原因" not in note and "全部原因" not in note


def test_no_pick_set_is_its_own_state(tmp_path):
    """上游空（盘前没选出候选）是第三种处境，不得与「跑了但全被否」同形。"""
    sf = _factory(tmp_path)
    missing = notification_diagnostics(DAY, session_factory=sf)
    assert missing["state"] == "no_pick_set"
    assert missing["pick_set"]["present"] is False
    assert missing["pick_set"]["count"] == 0

    _seed_pick_set(sf, items=[])                            # 有行但零候选
    empty_items = notification_diagnostics(DAY, session_factory=sf)
    assert empty_items["state"] == "no_pick_set"

    assert missing["state"] != "ran_rejected"


def test_ran_eligible_means_the_gap_is_elsewhere(tmp_path):
    """有通过判定的候选却仍空 ⇒ 才是需要排查的形态（应与全否态区分）。"""
    sf = _factory(tmp_path)
    _seed_pick_set(sf, items=[_cand(tier="executable", score=66.0)])
    _seed_snapshots(sf, symbol="603162", decision="notified", tier="executable", reason=None)
    got = notification_diagnostics(DAY, session_factory=sf)
    assert got["state"] == "ran_eligible"
    assert "落条目" in got["note"]


@pytest.mark.parametrize("decision", ["notified", "suppressed", "eligible"])
def test_all_eligible_decisions_count_as_should_have_appeared(tmp_path, decision):
    """`suppressed`（当日已推去重）与 `notified` 一样属于「本应有条目」，不得判成全否。"""
    sf = _factory(tmp_path)
    _seed_pick_set(sf, items=[_cand(tier="strong", score=80.0)])
    _seed_snapshots(sf, symbol="603162", decision=decision, tier="strong", reason=None)
    assert notification_diagnostics(DAY, session_factory=sf)["state"] == "ran_eligible"


# ---------------------------------------------------------------- 2. 不许把"无证据"写成"没有"

def test_read_failure_degrades_to_unavailable_not_empty(tmp_path):
    """读库失败 ⇒ `unavailable` + 说明，**不得**退化成"没有机会"。

    同族实测错（`BUG-016` 轮）：曾把 `opportunity_decision_snapshot` 的 **0 行**
    读作"买点循环未跑到判定阶段"，而真实原因是该表**表刚建立**。
    """
    def boom():
        raise RuntimeError("db is gone")

    got = notification_diagnostics(DAY, session_factory=boom)
    assert got["state"] == "unavailable"
    assert got["state"] not in ("no_run", "ran_rejected", "no_pick_set")
    assert "db is gone" in got["note"] and "不等于没有机会" in got["note"]


# ---------------------------------------------------------------- 3. 计数口径（易错点）

def test_repeated_polls_are_deduped_per_symbol(tmp_path):
    """同一只票被否 16 拍 ⇒ 唯一符号 1、原因计数 1；`polls` 单列体现活跃度。

    ⚠️ 若按记录数统计，会输出「16 条否决」——把 **1 只票**说成 **16 次问题**。
    """
    sf = _factory(tmp_path)
    _seed_pick_set(sf, items=[_cand()])
    _seed_snapshots(sf, symbol="603162", decision="rejected", tier="observe",
                    reason="置信档 observe 不足（需 executable/strong）", polls=16)

    d = notification_diagnostics(DAY, session_factory=sf)
    assert d["decisions"]["polls"] == 16                    # 拍数如实
    assert d["decisions"]["by_decision"] == {"rejected": 1}  # 去重后 = 1
    assert d["decisions"]["symbols"] == ["603162"]
    assert len(d["decisions"]["reasons"]) == 1
    assert d["decisions"]["reasons"][0]["count"] == 1        # 不是 16
    assert d["decisions"]["reasons"][0]["symbols"] == ["603162"]


def test_latest_record_wins_per_symbol(tmp_path):
    """同一只票先拒后过 ⇒ 取**最新**一条（否则会永远停在早期的否决上）。"""
    sf = _factory(tmp_path)
    _seed_pick_set(sf, items=[_cand(tier="strong", score=80.0)])
    _seed_snapshots(sf, symbol="603162", decision="rejected", tier="observe",
                    reason="置信档 observe 不足（需 executable/strong）", polls=1)
    _seed_snapshots(sf, symbol="603162", decision="notified", tier="strong",
                    reason=None, polls=1, step_minutes=5)

    d = notification_diagnostics(DAY, session_factory=sf)
    assert d["state"] == "ran_eligible"
    assert d["decisions"]["by_decision"] == {"notified": 1}
    assert d["decisions"]["top_tier"] == "strong"


def test_missing_reason_is_explicit_not_empty_string(tmp_path):
    """否决原因为空 ⇒ 显式写「未记录否决原因」，不吐空串（空串在前端等于没信息）。"""
    sf = _factory(tmp_path)
    _seed_pick_set(sf, items=[_cand()])
    _seed_snapshots(sf, symbol="603162", decision="rejected", tier="observe", reason=None)
    reasons = notification_diagnostics(DAY, session_factory=sf)["decisions"]["reasons"]
    assert reasons and reasons[0]["reason"] == "未记录否决原因"


def test_unknown_tier_is_not_silently_dropped(tmp_path):
    """未知档位单列进 `unknown_tiers`（fail-loud），不静默当作"没有档位"。

    ⚠️ 判"最高档"用**已知顺序**；将来新增档位时若不登记，这里必须能看见，
    否则 `top_tier` 会静默给出偏低的结论（用户据此会误判"差得远"）。
    """
    sf = _factory(tmp_path)
    _seed_pick_set(sf, items=[_cand(tier="future_tier", score=99.0)])
    _seed_snapshots(sf, symbol="603162", decision="rejected", tier="future_tier", reason="x")

    d = notification_diagnostics(DAY, session_factory=sf)
    assert d["decisions"]["unknown_tiers"] == ["future_tier"]
    assert d["decisions"]["top_tier"] is None or d["decisions"]["top_tier"] in TIER_ORDER


def test_top_tier_follows_declared_order(tmp_path):
    """最高档按 `TIER_ORDER`（strong > executable > observe）取，不受插入顺序影响。"""
    sf = _factory(tmp_path)
    _seed_pick_set(sf, items=[_cand("600001", tier="observe"), _cand("600002", tier="strong")])
    _seed_snapshots(sf, symbol="600001", decision="rejected", tier="observe", reason="a")
    _seed_snapshots(sf, symbol="600002", decision="rejected", tier="strong", reason="b")
    assert notification_diagnostics(DAY, session_factory=sf)["decisions"]["top_tier"] == "strong"


# ---------------------------------------------------------------- 4. 只看目标交易日

def test_other_trade_dates_do_not_leak_in(tmp_path):
    """只统计目标交易日：昨天的判定记录不得混进今天的诊断。"""
    sf = _factory(tmp_path)
    _seed_pick_set(sf, items=[_cand()])
    with sf() as db:
        db.add(OpportunityDecisionSnapshot(
            snapshot_id="other-day", run_id="r", trade_date="2026-09-15",
            as_of=datetime(2026, 9, 15, 13, 0), scenario="buy_point",
            stage="notification", symbol="600519", name="别的", source_theme="",
            decision="notified", rank=None, strategy_version="v", feature_version="v",
            data_state="ready", entry_price=None, evidence="{}",
        ))
        db.commit()

    d = notification_diagnostics(DAY, session_factory=sf)
    assert d["state"] == "no_run", "昨天有 notified 记录不得让今天判成 ran_eligible"
    assert d["decisions"]["symbols"] == []


def test_pick_set_gate_is_surfaced(tmp_path):
    """闸门（stand_aside + 相位 + 理由）必须透出 —— 它是"为什么空"的顶层原因之一。"""
    sf = _factory(tmp_path)
    _seed_pick_set(sf, items=[_cand()], gate={
        "stand_aside": True, "level": "strong", "phase": "退潮",
        "reasons": ["情绪相位「退潮」——赚钱效应处于周期低位"],
    })
    _seed_snapshots(sf, symbol="603162", decision="rejected", tier="observe", reason="x")

    g = notification_diagnostics(DAY, session_factory=sf)["pick_set"]["gate"]
    assert g["stand_aside"] is True
    assert g["phase"] == "退潮"
    assert g["reasons"]


def test_pick_set_counts_observation_and_buy_range(tmp_path):
    """候选侧的可参与性摘要（仅观察 / 无买入区间）要计数如实。"""
    sf = _factory(tmp_path)
    _seed_pick_set(sf, items=[
        _cand("600001", observation_only=True, buy_range=None),
        _cand("600002", observation_only=False, buy_range=[10.0, 10.6]),
    ])
    d = notification_diagnostics(DAY, session_factory=sf)
    assert d["pick_set"]["count"] == 2
    assert d["pick_set"]["observation_only"] == 1
    assert d["pick_set"]["no_buy_range"] == 1


# ---------------------------------------------------------------- 5. 只读保证

def test_diagnostics_never_writes(tmp_path):
    """本模块是**只读**的：跑完之后两张表的行数必须逐字不变。"""
    sf = _factory(tmp_path)
    _seed_pick_set(sf, items=[_cand()])
    _seed_snapshots(sf, symbol="603162", decision="rejected", tier="observe", reason="x")

    def counts():
        with sf() as db:
            return (
                db.query(DailyPickSet).count(),
                db.query(OpportunityDecisionSnapshot).count(),
            )

    before = counts()
    notification_diagnostics(DAY, session_factory=sf)
    assert counts() == before
