"""战法核验结论登记（`app/research/verify_registry.py` + `strategy_verify.gate_verdict`）单测。

S2-11 背景：`strategy_verify.py` 是离线重计算（duckdb 全历史），结论此前只流向
脚本 stdout，登记册的 ⛔/🟡 状态靠人工誊写 ⇒ **无时间戳、不可回查、无背书**。
本测试守的是「落盘 → 读回 → 接进登记册」这条链，以及 **verdict 必须由判据算出
而不是写死**——后者一旦退化，登记册就退回"谁都能改状态"的老问题。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.research import strategy_verify as sv
from app.research import verify_registry as vr

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------- summarize_row


def test_summarize_row_extracts_all_six_quantities():
    """六个量必须全给：只看均值会被右偏分布骗（候选B 就是反例）。"""
    row = {"n": 100, "n5": 98, "m5": 1.33, "med5": -0.08, "w5": 0.493, "s5": 6.2, "x5": 0.85}
    out = sv.summarize_row(row, horizon=5)
    assert out["n"] == 98
    assert out["mean"] == 1.33
    assert out["median"] == -0.08
    assert out["win_rate"] == 0.493
    assert out["std"] == 6.2
    assert out["excess"] == 0.85
    assert out["horizon"] == 5


def test_summarize_row_tolerates_missing_and_none():
    """缺窗口列 / 值为 None 时不得抛异常——核验结果本就可能没有某个窗口。"""
    out = sv.summarize_row({"n": 7}, horizon=20)
    assert out["n"] == 7
    assert out["mean"] is None and out["excess"] is None
    assert sv.summarize_row({"n3": None, "m3": None}, horizon=3)["mean"] is None


# ---------------------------------------------------------------- gate_verdict


def _ok_metrics(**over) -> dict:
    m = {"n": 5000, "mean": 1.6, "median": 0.9, "win_rate": 0.57, "std": 6.0, "excess": 1.4}
    m.update(over)
    return m


def test_gate_verdict_passes_when_all_clauses_met():
    g = sv.gate_verdict(_ok_metrics(), yearly_pos=10, yearly_tot=11, limit_up_share=0.05,
                        excess_median=0.9, excess_win_rate=0.57)
    assert g["verdict"] == sv.VERDICT_PASS
    assert g["failed"] == []
    assert g["unchecked"] == []


def test_gate_verdict_rejects_non_positive_excess():
    """市场中性超额 ≤ 0 是唯一直接否决项：连同日市场均值都跑不赢，无从谈 alpha。"""
    g = sv.gate_verdict(_ok_metrics(excess=-0.2), yearly_pos=9, yearly_tot=11)
    assert g["verdict"] == sv.VERDICT_REJECT
    assert any("≤ 0" in f for f in g["failed"])


def test_gate_verdict_observes_when_median_or_winrate_fails():
    """均值正但**中性**中位 ≤0 / 跑赢 <50% ⇒ 收益右偏，**降级不否决**。

    这正是候选B 的真实情形（2026-09-11 实测：中性中位 −0.08%、跑赢 49.3%）。
    """
    g = sv.gate_verdict(_ok_metrics(), yearly_pos=9, yearly_tot=11,
                        excess_median=-0.08, excess_win_rate=0.493)
    assert g["verdict"] == sv.VERDICT_OBSERVE
    assert any("右偏" in f for f in g["failed"])
    assert any("跑赢比例" in f for f in g["failed"])


def test_gate_verdict_ignores_raw_median_and_winrate():
    """**定点回归**：原始口径的中位/胜率**不得**被当作判据。

    候选B 原始中位 +3.33%、跑赢 68.1%（看着全达标），中性口径却是 −0.08% / 49.3%
    （不达标）。若此处误用原始口径，判据在上涨市里恒真，闸门形同虚设。
    """
    g = sv.gate_verdict(_ok_metrics(median=-0.08, win_rate=0.493), yearly_pos=9, yearly_tot=11)
    assert g["verdict"] == sv.VERDICT_PASS, "原始口径不得影响判定"
    assert g["failed"] == []
    # 但必须显式声明"未验"，绝不能伪装成已验
    assert any("中性中位未验" in u for u in g["unchecked"])
    assert any("中性跑赢比例未验" in u for u in g["unchecked"])
    assert "未验" in g["note"]


def test_gate_verdict_unchecked_needs_neutral_caliber():
    """拿不到中性口径时**跳过**判据并记 `unchecked`，不静默放行。"""
    g = sv.gate_verdict(_ok_metrics(), yearly_pos=10, yearly_tot=11)
    assert g["verdict"] == sv.VERDICT_PASS
    assert len(g["unchecked"]) == 2
    assert "未验" in g["note"]


def test_gate_verdict_observes_unstable_years():
    g = sv.gate_verdict(_ok_metrics(), yearly_pos=2, yearly_tot=11)
    assert g["verdict"] == sv.VERDICT_OBSERVE
    assert any("不稳定" in f for f in g["failed"])


def test_gate_verdict_observes_untradable_limit_up_share():
    g = sv.gate_verdict(_ok_metrics(), yearly_pos=10, yearly_tot=11, limit_up_share=0.42)
    assert g["verdict"] == sv.VERDICT_OBSERVE
    assert any("难成交" in f for f in g["failed"])


def test_gate_verdict_thin_sample_is_observe_not_reject():
    """样本少 ≠ 无效。样本不足必须落 observe，**绝不**因为样本少就否决。"""
    g = sv.gate_verdict(_ok_metrics(n=12))
    assert g["verdict"] == sv.VERDICT_OBSERVE
    assert any("样本不足" in f for f in g["failed"])


def test_gate_verdict_reports_every_failed_clause():
    """`failed` 给全部命中项——「哪一项不达标」比「达不达标」更有诊断价值。"""
    g = sv.gate_verdict(_ok_metrics(n=10, excess=-1.0),
                        yearly_pos=1, yearly_tot=10, limit_up_share=0.9,
                        excess_median=-2.0, excess_win_rate=0.3)
    assert g["verdict"] == sv.VERDICT_REJECT
    assert len(g["failed"]) == 6


# ---------------------------------------------------------------- save / load


@pytest.fixture()
def vdir(tmp_path, monkeypatch):
    d = tmp_path / "verify"
    monkeypatch.setattr(vr, "VERIFY_DIR", d)
    return d


def test_save_and_load_roundtrip(vdir):
    p = vr.save_record("demo", verdict=vr.VERDICT_REJECT, headline="−0.51% ⇒ 否决",
                       metrics={"n": 100}, sample={"trade_days": 2400},
                       source="scripts/demo.py")
    assert p == vdir / "demo.json"
    rec = vr.load_record("demo")
    assert rec is not None
    assert rec["verdict"] == "reject"
    assert rec["headline"] == "−0.51% ⇒ 否决"
    assert rec["sample"]["trade_days"] == 2400
    assert rec["recorded_at"]  # 必须有时间戳（此前人工誊写的结论没有）


def test_save_record_rejects_unknown_verdict(vdir):
    with pytest.raises(ValueError):
        vr.save_record("demo", verdict="maybe", headline="x")


def test_save_record_rejects_blank_key(vdir):
    with pytest.raises(ValueError):
        vr.save_record("  ", verdict=vr.VERDICT_PASS, headline="x")


def test_save_record_sanitizes_key(vdir):
    """键直接进文件名 ⇒ 必须挡掉路径穿越与非法字符。"""
    p = vr.save_record("../../etc/pass wd", verdict=vr.VERDICT_PASS, headline="x")
    assert p.parent == vdir
    assert vr.load_record("../../etc/pass wd") is not None


def test_save_record_overwrites_keeps_latest(vdir):
    vr.save_record("k", verdict=vr.VERDICT_OBSERVE, headline="旧")
    vr.save_record("k", verdict=vr.VERDICT_REJECT, headline="新")
    assert vr.load_record("k")["headline"] == "新"


def test_load_record_missing_returns_none(vdir):
    assert vr.load_record("nope") is None


def test_load_record_corrupt_json_returns_none(vdir):
    vdir.mkdir(parents=True, exist_ok=True)
    (vdir / "broken.json").write_text("{ not json", encoding="utf-8")
    assert vr.load_record("broken") is None


# ---------------------------------------------------------------- verification_of 三态


def test_verification_of_unavailable_when_absent(vdir):
    v = vr.verification_of("ghost")
    assert v["available"] is False
    assert v["verdict"] is None
    assert v["reason"]


def test_verification_of_available_and_fresh(vdir):
    vr.save_record("k", verdict=vr.VERDICT_OBSERVE, headline="右偏", metrics={"n": 3000})
    v = vr.verification_of("k")
    assert v["available"] is True
    assert v["stale"] is False
    assert v["age_days"] == 0
    assert v["verdict"] == "observe"
    assert v["reason"] is None


def test_verification_of_flags_stale_with_age(vdir):
    """超期**只标注不删除**——历史结论本身是有价值的证据链。"""
    vr.save_record("k", verdict=vr.VERDICT_PASS, headline="旧结论")
    v = vr.verification_of("k", max_age_days=-1)  # 任何年龄都算超期
    assert v["available"] is True
    assert v["stale"] is True


def test_verification_of_survives_corrupt_file(vdir):
    vdir.mkdir(parents=True, exist_ok=True)
    (vdir / "k.json").write_text("garbage", encoding="utf-8")
    v = vr.verification_of("k")
    assert v["available"] is False and v["reason"]


def test_list_records_sorted_by_time_desc(vdir):
    vr.save_record("a", verdict=vr.VERDICT_PASS, headline="A")
    vr.save_record("b", verdict=vr.VERDICT_PASS, headline="B")
    recs = vr.list_records()
    assert len(recs) == 2
    assert [r["key"] for r in recs] == ["b", "a"]  # 后写的在前


def test_list_records_empty_when_dir_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(vr, "VERIFY_DIR", tmp_path / "nope")
    assert vr.list_records() == []


# ---------------------------------------------------------------- 闭环守卫


def test_verification_scripts_persist_their_conclusion():
    """**定点回归**：两个核验脚本必须落盘。

    收敛前它们只 `print()`，结论跑完即散，登记册状态靠人抄 —— 这正是 S2-11 要治的病。
    任一脚本删掉落盘调用即失败。
    """
    for name, key in (("verify_two_thirty_five.py", "two_thirty_five"),
                      ("verify_candidate_b_oos.py", "pullback_reversal")):
        src = (ROOT / "scripts" / name).read_text(encoding="utf-8")
        assert "save_record" in src, f"{name} 不再落盘了"
        assert key in src, f"{name} 落盘的键不是 {key}"


def test_verdict_is_computed_not_hardcoded():
    """**定点回归**：verdict 必须来自 `gate_verdict`，不得写死字符串字面量。

    写死 = 重跑后数据变了结论不变 = 回到"状态无背书"。
    """
    for name in ("verify_two_thirty_five.py", "verify_candidate_b_oos.py"):
        src = (ROOT / "scripts" / name).read_text(encoding="utf-8")
        assert "gate_verdict" in src, f"{name} 没有走准入判据"
        for bad in ('verdict="reject"', 'verdict="pass"', 'verdict="observe"',
                    "verdict='reject'", "verdict='pass'", "verdict='observe'"):
            assert bad not in src, f"{name} 把 verdict 写死了（{bad}）"


def test_registry_exposes_verification_for_keys_with_verify_key():
    """登记册带 `verify_key` 的条目必须附 `verification` 三态字段。"""
    from app.picks.strategy_registry import list_strategy_keys

    items = {i["key"]: i for i in list_strategy_keys()}
    for key in ("pullback_reversal", "two_thirty_five"):
        assert "verification" in items[key], f"{key} 缺核验背书"
        v = items[key]["verification"]
        assert "available" in v
        # 未跑核验时 available=False 且带 reason，而不是字段缺失
        assert ("verdict" in v) and ("reason" in v)


def test_registry_survives_verify_registry_failure(monkeypatch):
    """核验产物读取炸了不能把整个登记册端点拖成 500——一项读失败，其余照出。"""
    import app.picks.strategy_registry as sr
    import app.research.verify_registry as vrmod

    def _boom(_key, **_kw):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(vrmod, "verification_of", _boom)
    items = sr.list_strategy_keys()
    item = next(i for i in items if i["key"] == "two_thirty_five")
    assert item["verification"]["available"] is False
    assert "disk on fire" in (item["verification"]["reason"] or "")
    # 其余条目不受影响
    assert any(i["key"] == "daily_picks" for i in items)


def test_registry_note_matches_verification_when_available(vdir, monkeypatch):
    """有产物时，`headline` 必须来自产物（而非 `note` 的静态文字）"""
    vr.save_record("two_thirty_five", verdict=vr.VERDICT_REJECT,
                   headline="实测：五步全通过 −0.51% ⇒ 否决")
    from app.picks.strategy_registry import list_strategy_keys

    item = next(i for i in list_strategy_keys() if i["key"] == "two_thirty_five")
    assert item["verification"]["available"] is True
    assert item["verification"]["headline"].startswith("实测")


def test_verify_dir_is_inside_data_dir():
    """产物必须落在 `data/` 下（与 `factors/eval_report.json` 同族），不污染代码目录。"""
    assert "data" in vr.VERIFY_DIR.parts
    assert vr.VERIFY_DIR.name == "verify"


def test_no_tmp_file_left_after_save(vdir):
    """原子写用完必须清掉临时文件，否则目录里会积一堆 .json.tmp。"""
    vr.save_record("k", verdict=vr.VERDICT_PASS, headline="x")
    assert list(vdir.glob("*.tmp")) == []


def test_recorded_at_is_beijing_naive(vdir):
    """时间戳必须是北京 naive（与告警/事件同口径），不带时区标记也不带 +08:00。"""
    from datetime import datetime

    vr.save_record("k", verdict=vr.VERDICT_PASS, headline="x")
    ts = vr.load_record("k")["recorded_at"]
    dt = datetime.fromisoformat(ts)
    assert dt.tzinfo is None
    assert "+" not in ts and ts[-1] != "Z"
