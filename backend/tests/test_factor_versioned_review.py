"""GOV-001 守卫：研究结论的**版本化复核清单**（报告 F5）。

## 防的是什么

口径（年化 / IC 成熟度 / 分位切点）一改，**此前所有基于旧口径的结论都应当被复核**。
此前只有「翻转项」留痕（`recheck`），而**未翻转 ≠ 不受影响**——旧结论的数字是旧口径
算出来的，换口径后它只是"恰好还停在同一档"，不能被继续当调参依据。

实测活案例（2026-09-14）：磁盘报告产出于 `2026-09-07T17:00:46`（`algo_version` 字段
尚不存在），而代码口径已到 `2026-09-14.ic-maturity-v3`；`report.freshness()` 因
`age_days=7 < 40` 判 `stale=False` ⇒ **旧口径的 PASS 因子照样以 `stale: False` 送进
进化议程的 `factor_ic` 证据**。这正是报告 F5 说的「旧报告继续被当调参依据」。

## 三态（本文件的核心纪律）

| 报告声明的口径 | `algo_current` | 复核清单 | 为什么 |
|---|---|---|---|
| == 当前 | `True` | 空（翻转项另由 `recheck` 承载） | 确认为同一版本 |
| != 当前 | `False` | **全部**旧结论（含未翻转） | 旧数字须复核 |
| **无该字段** | `None`（**未判定**） | **同样全部列出** | 不可知 ≠ 可以继续用 |

最后一行是刻意设计：`None` **不得**塌缩成 `False`（§1「三态 > 二态」），
也**不得**被当成"没问题"而放过——旧报告不可知恰恰更需要人看一眼。
"""
from __future__ import annotations

import json
from datetime import datetime

from app.factors import evaluate as ev
from app.factors import report as fr

#: 区分「不写该字段」与「写 None」——三态测试必须能构造"无声明"形态。
_UNSET = object()


def _write_report(
    tmp_path, *, generated_at: str | None, algo_version=_UNSET,
    factors=None, summary=None,
):
    p = tmp_path / "eval_report.json"
    body = {
        "generated_at": generated_at,
        "protocol": {}, "universe": {}, "data_quality": {},
        "factors": factors if factors is not None else [],
        "summary": summary if summary is not None else {"pass": [], "conditional": [], "fail": []},
    }
    if algo_version is not _UNSET:
        body["algo_version"] = algo_version
    p.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    return p


def _sample_factors():
    return [
        {"name": "mom20", "category": "momentum", "verdict": "PASS", "coverage": 0.99,
         "best_horizon": 20, "windows": {"20": {"ic_mean": 0.031, "icir": 0.31, "n_days": 2419}}},
    ]


# ---------------------------------------------------------------- 版本化留存（归档）

def test_archive_previous_writes_versioned_copy(tmp_path):
    """归档名须含**旧报告自己声明的口径**与生成时间——复核清单要靠它回溯「哪些结论是旧口径算的」。"""
    src = tmp_path / "eval_report.json"
    src.write_text(json.dumps({
        "generated_at": "2026-09-07T17:00:46",
        "algo_version": "2026-09-07.legacy",
        "factors": [{"name": "mom20", "verdict": "PASS"}],
    }, ensure_ascii=False), encoding="utf-8")

    dst = ev._archive_previous(src)

    assert dst is not None and dst.exists()
    assert dst.parent.name == "history"
    assert dst.parent.parent == tmp_path          # 归档**跟随报告落点**，不锚死模块常量
    assert "2026-09-07.legacy" in dst.name
    assert "2026-09-07T170046" in dst.name
    assert ":" not in dst.name                    # 冒号已去（跨文件系统安全）
    assert json.loads(dst.read_text(encoding="utf-8"))["factors"][0]["name"] == "mom20"


def test_archive_previous_undeclared_version_uses_placeholder(tmp_path):
    """无 `algo_version` 的旧报告仍须可归档（否则历史结论永远丢）。"""
    src = tmp_path / "eval_report.json"
    src.write_text(json.dumps({"generated_at": "2026-09-07T17:00:46", "factors": []}),
                   encoding="utf-8")
    dst = ev._archive_previous(src)
    assert dst is not None and "unknown" in dst.name


def test_archive_previous_is_idempotent(tmp_path):
    """幂等：重复归档同一份旧报告不产生新文件、**不重写**已有归档。"""
    src = tmp_path / "eval_report.json"
    src.write_text(json.dumps({"generated_at": "2026-09-07T17:00:46", "factors": []}),
                   encoding="utf-8")

    first = ev._archive_previous(src)
    first.write_text("TAMPERED", encoding="utf-8")      # 若被重写，哨兵会消失
    again = ev._archive_previous(src)

    assert again == first
    assert first.read_text(encoding="utf-8") == "TAMPERED"
    assert len(list((tmp_path / "history").iterdir())) == 1


def test_archive_previous_missing_or_corrupt_returns_none(tmp_path):
    """三态：无可归档 ⇒ None，**不阻塞评估**（与 `_prev_verdicts` 同姿势）。"""
    assert ev._archive_previous(None) is None
    assert ev._archive_previous(tmp_path / "nope.json") is None
    bad = tmp_path / "eval_report.json"
    bad.write_text("{not json", encoding="utf-8")
    assert ev._archive_previous(bad) is None


# ---------------------------------------------------------------- 口径版本读取（三态）

def test_prev_algo_version_three_states(tmp_path):
    """有声明 → 该值；**有报告但无字段 → None（未判定）**；无报告 → None。"""
    p = tmp_path / "eval_report.json"
    assert ev._prev_algo_version(None) is None
    assert ev._prev_algo_version(p) is None                       # 文件不存在
    p.write_text(json.dumps({"generated_at": "x", "factors": []}), encoding="utf-8")
    assert ev._prev_algo_version(p) is None                       # 无该字段 = 未判定
    p.write_text(json.dumps({"algo_version": "2026-09-07.legacy"}), encoding="utf-8")
    assert ev._prev_algo_version(p) == "2026-09-07.legacy"


# ---------------------------------------------------------------- 复核清单（核心）

def test_review_required_lists_unchanged_old_conclusions():
    """**核心断言**：口径变更时，三态**未变**的因子同样进复核清单。

    只列翻转项是错的——旧结论是旧口径算的数，换口径就该复核，哪怕结论停在原档。
    """
    results = [
        {"name": "a", "verdict": "PASS"},        # 旧 PASS → 现 PASS（**未翻转**）
        {"name": "b", "verdict": "FAIL"},        # 旧 PASS → 现 FAIL（翻转）
    ]
    out = ev._build_review_required(results, {"a": "PASS", "b": "PASS"}, "old-v1", "new-v2")

    assert [x["name"] for x in out] == ["a", "b"]     # a 必须在内
    assert [x["changed"] for x in out] == [False, True]
    assert all("old-v1" in x["reason"] for x in out)


def test_review_required_empty_when_same_algo_version():
    """口径确认为同一版本 ⇒ 空清单（翻转项由 `recheck` 承载，两清单职责不重叠）。"""
    results = [{"name": "a", "verdict": "PASS"}, {"name": "b", "verdict": "FAIL"}]
    assert ev._build_review_required(results, {"a": "PASS", "b": "PASS"}, "v1", "v1") == []


def test_review_required_flags_undeclared_prev_algo():
    """旧报告**未声明**口径 ⇒ 未判定，仍须列出（2026-09-14 活案例形态）。

    若把 `None` 当成"未变更"放过，磁盘上那份 09-07 报告就会继续被当现状用。
    """
    results = [{"name": "a", "verdict": "PASS"}]
    out = ev._build_review_required(results, {"a": "PASS"}, None, "2026-09-14.ic-maturity-v3")

    assert len(out) == 1
    assert "未判定" in out[0]["reason"]
    assert "2026-09-14.ic-maturity-v3" in out[0]["reason"]


def test_review_required_empty_without_history():
    """首次跑（无旧结论）⇒ 空，不臆断。"""
    assert ev._build_review_required([{"name": "a", "verdict": "PASS"}], {}, None, "v1") == []
    assert ev._build_review_required([], {}, "old", "new") == []


def test_review_required_skips_factors_absent_from_old_report():
    """旧口径下本无该因子的结论（新增因子）⇒ 无历史结论可复核。"""
    results = [{"name": "a", "verdict": "PASS"}, {"name": "brand_new", "verdict": "PASS"}]
    out = ev._build_review_required(results, {"a": "CONDITIONAL"}, "old", "new")
    assert [x["name"] for x in out] == ["a"]


# ---------------------------------------------------------------- 只读层（口径维度）

def test_freshness_algo_current_three_states(monkeypatch, tmp_path):
    now = datetime.now().isoformat()

    monkeypatch.setattr(fr, "REPORT_PATH",
                        _write_report(tmp_path, generated_at=now, algo_version=ev.ALGO_VERSION))
    same = fr.freshness()
    assert same["algo_current"] is True
    assert same["algo_version"] == ev.ALGO_VERSION
    assert same["algo_note"] is None

    monkeypatch.setattr(fr, "REPORT_PATH",
                        _write_report(tmp_path, generated_at=now, algo_version="2026-09-07.legacy"))
    changed = fr.freshness()
    assert changed["algo_current"] is False
    assert "2026-09-07.legacy" in changed["algo_note"]

    monkeypatch.setattr(fr, "REPORT_PATH", _write_report(tmp_path, generated_at=now))
    unknown = fr.freshness()
    assert unknown["algo_current"] is None            # 未判定，不塌缩成 False
    assert "未判定" in unknown["algo_note"]


def test_freshness_stale_stays_time_only(monkeypatch, tmp_path):
    """**纯增量守卫**：口径过期但时间未超期 ⇒ `stale` 仍为 False。

    口径维度必须是**独立字段**（`algo_current`），不得把两种陈旧混进一个布尔——
    否则既有消费方（调度器「该重跑了」判定）会被口径变更误触发。
    """
    monkeypatch.setattr(
        fr, "REPORT_PATH",
        _write_report(tmp_path, generated_at=datetime.now().isoformat(),
                      algo_version="2026-01-01.ancient"),
    )
    f = fr.freshness(max_age_days=40)

    assert f["stale"] is False             # 时间维度：7 天前，未超 40 天宽限
    assert f["algo_current"] is False      # 口径维度：与当前不同源
    assert f["reason"] is None             # reason 仍是时间语义，不被口径污染


def test_ic_evidence_exposes_algo_dimension(monkeypatch, tmp_path):
    """消费点（进化议程 `factor_ic`）必须能看出"这份结论是否与当前口径同源"。"""
    monkeypatch.setattr(
        fr, "REPORT_PATH",
        _write_report(tmp_path, generated_at=datetime.now().isoformat(),
                      algo_version="2026-09-07.legacy", factors=_sample_factors(),
                      summary={"pass": ["mom20"], "conditional": [], "fail": []}),
    )
    out = fr.ic_evidence()

    assert out["available"] is True
    assert out["algo_version"] == "2026-09-07.legacy"
    assert out["algo_current"] is False
    assert "复核" in out["algo_note"]
    # 既有形状不被破坏（消费方契约）
    assert out["counts"] == {"pass": 1, "conditional": 0, "fail": 0}
    assert "样本外" in out["caveat"]
