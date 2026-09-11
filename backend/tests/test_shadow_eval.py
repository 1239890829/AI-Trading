"""影子参数离线对照评估（`app/services/shadow_eval.py`）单测。

S2-11 背景：白名单从 1 扩到 5 之后，评估器仍只有 `picks_style_offsets_json` 一个，
其余四个一律 `shadow_eval_unsupported` —— 影子队列里躺着"没人验"的变更。

本测试守三件事：
1. **模拟是纯函数**（不依赖 DB），可精确断言；
2. **样本不足必须 insufficient**，绝不硬给方向；
3. **`unsupported`（没能力）与 `insufficient`（样本不够）不得混用** ——
   前者是"缺评估器"的永久状态，后者是"再攒几天数据就能判"的暂时状态，
   混为一谈会让人以为缺的是能力，从而放弃等待。
"""
from __future__ import annotations

import pytest

from app.services import shadow_eval as se


def _sets(n: int = 4, *, scores=(70.0, 60.0, 52.0, 51.0), rejected=(49.0, 48.0, 47.0)):
    """构造 n 日历史：入选 4 只 + 落选 3 只，分数固定便于推演。"""
    out = []
    for i in range(n):
        out.append({
            "date": f"2026-09-{i + 1:02d}",
            "items": [{"symbol": f"S{j}", "score": s} for j, s in enumerate(scores)],
            "rejected": [{"symbol": f"R{j}", "score": s} for j, s in enumerate(rejected)],
            "replaced": [{"out": "S3", "in": "R0", "delta": 15.6}],
        })
    return out


def _reviews(dates_symbols_verdict):
    return {(d, s): {"verdict": v, "excess_pct": 1.0} for d, s, v in dates_symbols_verdict}


# ---------------------------------------------------------------- 纯函数模拟


def test_simulate_min_score_drops_below_threshold():
    sim = se.simulate_min_score(_sets(1), threshold=55.0, capacity=5)
    assert len(sim["dropped"]) == 2, "低于 55 的两只应出列"
    assert len(sim["kept"]) == 2
    assert sim["added"] == [], "落选者最高 49 < 55，补不进"


def test_simulate_min_score_backfills_only_newly_eligible():
    """补位只补「旧门槛够不着、新门槛够得着」的那批（base=50，新=45 ⇒ 49/48/47 全部）。"""
    sim = se.simulate_min_score(_sets(1), threshold=45.0, capacity=5, base_threshold=50.0)
    assert sim["dropped"] == []
    assert [a["symbol"] for a in sim["added"]] == ["R0"], "容量 5、已占 4 ⇒ 只补 1 只（最高分）"


def test_simulate_does_not_backfill_when_threshold_unchanged():
    """**定点回归**：门槛不变时**不得**凭空补人。

    历史上组合没填满容量，往往是被板块去重/深评上限等其它约束卡住，
    而不是"他们不够分"。宽松补位会把这些约束当成不存在，
    产出一个历史上从未发生过的组合。
    """
    sim = se.simulate_min_score(_sets(1), threshold=40.0, capacity=5, base_threshold=40.0)
    assert sim["added"] == []
    assert sim["dropped"] == []
    assert sim["size_after"] == sim["size_before"]


def test_simulate_does_not_backfill_when_raising():
    """抬高门槛时不存在"新释放"的候选（他们原本就不够旧门槛）。"""
    sim = se.simulate_min_score(_sets(1), threshold=55.0, capacity=5, base_threshold=50.0)
    assert sim["added"] == []


def test_simulate_respects_capacity():
    """补位不得超出容量——否则"放宽门槛"会变成"无上限放人进来"。"""
    sim = se.simulate_min_score(_sets(1), threshold=45.0, capacity=5, base_threshold=50.0)
    assert len(sim["kept"]) + len(sim["added"]) <= 5


def test_simulate_ignores_records_without_score():
    """缺 score 的记录不得被当成 0 分剔除（那会静默清空组合）。"""
    sets = [{"date": "d", "items": [{"symbol": "A"}, {"symbol": "B", "score": 80}],
             "rejected": [], "replaced": []}]
    sim = se.simulate_min_score(sets, threshold=50.0, capacity=5)
    assert [i["symbol"] for i in sim["kept"]] == ["B"]
    assert [i["symbol"] for i in sim["dropped"]] == []


def test_simulate_replace_threshold_splits_by_delta():
    sets = [{"date": "d", "items": [], "rejected": [],
             "replaced": [{"out": "A", "in": "B", "delta": 5.0},
                          {"out": "C", "in": "D", "delta": 25.0},
                          {"out": "E", "in": "F"}]}]
    sim = se.simulate_replace_threshold(sets, threshold=15.0)
    assert [r["delta"] for r in sim["blocked"]] == [5.0]
    assert [r["delta"] for r in sim["allowed"]] == [25.0]
    assert sim["without_delta"] == 1, "无 delta 的记录要单独计数，不能当 0 处理"


# ---------------------------------------------------------------- 三态与样本门槛


def test_insufficient_when_history_too_short():
    out = se.eval_min_pick_score(before=50.0, after=55.0, sets=_sets(2), reviews={})
    assert out["verdict"] == se.VERDICT_INSUFFICIENT
    assert "需 ≥" in out["note"]


def test_insufficient_on_unparsable_values():
    out = se.eval_min_pick_score(before="x", after="y", sets=_sets(5), reviews={})
    assert out["verdict"] == se.VERDICT_INSUFFICIENT


def test_neutral_when_nothing_changes():
    """门槛不变 ⇒ 组合构成不变 ⇒ 不该强行给方向。"""
    out = se.eval_min_pick_score(before=50.0, after=50.0, sets=_sets(5), reviews={})
    assert out["verdict"] == se.VERDICT_NEUTRAL
    assert "不改变" in out["note"]
    assert out["metrics"]["dropped"] == 0 and out["metrics"]["added"] == 0


def test_neutral_with_impact_but_too_few_reviews():
    """有影响面但复盘不足 ⇒ 给影响面、不给方向（**不是** insufficient）。"""
    out = se.eval_min_pick_score(before=50.0, after=55.0, sets=_sets(5), reviews={})
    assert out["verdict"] == se.VERDICT_NEUTRAL
    assert "只给影响面" in out["note"]
    assert out["metrics"]["dropped"] > 0


# ---------------------------------------------------------------- 方向判定


def _rich_reviews(sets, *, dropped_good: bool):
    """给被剔除者/保留者分别打 good/bad，构造两个方向。"""
    sim = se.simulate_min_score(sets, threshold=55.0, capacity=5, base_threshold=50.0)
    rev = {}
    for r in sim["dropped"]:
        rev[(r["date"], r["symbol"])] = {"verdict": "good" if dropped_good else "bad",
                                         "excess_pct": 1.0}
    for r in sim["kept"]:
        rev[(r["date"], r["symbol"])] = {"verdict": "good", "excess_pct": 2.0}
    return rev


def test_raising_threshold_supported_when_dropped_are_weaker():
    sets = _sets(6)
    rev = _rich_reviews(sets, dropped_good=False)
    out = se.eval_min_pick_score(before=50.0, after=55.0, sets=sets, reviews=rev)
    assert out["verdict"] == se.VERDICT_SUPPORTS


def test_raising_threshold_opposed_when_dropped_are_not_weaker():
    """被剔除者与保留者同样优秀 ⇒ 抬高门槛是误杀。"""
    sets = _sets(6)
    rev = _rich_reviews(sets, dropped_good=True)
    out = se.eval_min_pick_score(before=50.0, after=55.0, sets=sets, reviews=rev)
    assert out["verdict"] == se.VERDICT_OPPOSES


def test_lowering_threshold_judges_the_entrants():
    """放宽门槛时，判据落在**补入者**身上（剔除者此时为空）。"""
    sets = _sets(6)
    rev = {}
    sim = se.simulate_min_score(sets, threshold=45.0, capacity=5, base_threshold=50.0)
    assert len(sim["added"]) >= se.MIN_REVIEW_SAMPLES, "构造的补入者要够复盘样本下限"
    for r in sim["added"]:
        rev[(r["date"], r["symbol"])] = {"verdict": "good", "excess_pct": 3.0}
    for r in sim["kept"]:
        rev[(r["date"], r["symbol"])] = {"verdict": "good", "excess_pct": 1.0}
    out = se.eval_min_pick_score(before=50.0, after=45.0, sets=sets, reviews=rev)
    assert out["verdict"] == se.VERDICT_SUPPORTS


def test_lowering_threshold_explains_the_review_blind_spot():
    """放宽方向的**结构性盲区**必须说清：补入者来自落选池，复盘表里没有他们。

    若只报"复盘不足 5 条"，会被读成"这次变更影响很小"，而真实含义是
    「这一类变更目前无法用复盘数据验证」——两者处置完全不同。
    """
    sets = _sets(6)
    out = se.eval_min_pick_score(before=50.0, after=45.0, sets=sets, reviews={})
    assert out["verdict"] == se.VERDICT_NEUTRAL
    assert out["metrics"]["added"] > 0, "确实有补位，否则测不到这个盲区"
    assert "落选池" in out["note"]
    assert "无复盘记录" in out["note"]


def test_lowering_threshold_opposed_when_entrants_are_weaker():
    """补进来的比原成员差 ⇒ 放宽门槛是稀释组合。"""
    sets = _sets(6)
    rev = {}
    sim = se.simulate_min_score(sets, threshold=45.0, capacity=5, base_threshold=50.0)
    for r in sim["added"]:
        rev[(r["date"], r["symbol"])] = {"verdict": "bad", "excess_pct": -2.0}
    for r in sim["kept"]:
        rev[(r["date"], r["symbol"])] = {"verdict": "good", "excess_pct": 1.0}
    out = se.eval_min_pick_score(before=50.0, after=45.0, sets=sets, reviews=rev)
    assert out["verdict"] == se.VERDICT_OPPOSES


def test_avg_size_prevents_misreading_totals():
    """总量是 N 日累计，日均才是"组合有多大"——两个都要有。"""
    out = se.eval_min_pick_score(before=50.0, after=55.0, sets=_sets(4), reviews={})
    m = out["metrics"]
    assert m["size_before_total"] == 16  # 4 日 × 4 只
    assert m["avg_size_before"] == 4.0
    assert m["avg_size_after"] < m["avg_size_before"]


# ---------------------------------------------------------------- 换股门槛


def test_replace_threshold_reports_no_effect_explicitly():
    """文档说该门槛"几乎不起作用"——评估器要能给出这个**实测**结论。"""
    sets = [{"date": f"d{i}", "items": [], "rejected": [],
             "replaced": [{"out": "A", "in": "B", "delta": 30.0}]} for i in range(5)]
    out = se.eval_replace_threshold(before=15.0, after=20.0, sets=sets, reviews={})
    assert out["verdict"] == se.VERDICT_NEUTRAL
    assert "不起作用" in out["note"]


def test_replace_threshold_no_swaps_is_neutral():
    sets = [{"date": f"d{i}", "items": [], "rejected": [], "replaced": []} for i in range(5)]
    out = se.eval_replace_threshold(before=15.0, after=20.0, sets=sets, reviews={})
    assert "没有换股记录" in out["note"]


def test_replace_threshold_opposes_when_blocked_swaps_were_good():
    """被拦下的换股里，换入方事后更优 ⇒ 抬高门槛是拦错了。"""
    sets = []
    reviews = {}
    for i in range(6):
        date = f"d{i}"
        sets.append({"date": date, "items": [], "rejected": [],
                     "replaced": [{"out": "OUT", "in": "IN", "delta": 5.0}]})
        reviews[(date, "IN")] = {"verdict": "good", "excess_pct": 5.0}
        reviews[(date, "OUT")] = {"verdict": "bad", "excess_pct": -1.0}
    out = se.eval_replace_threshold(before=15.0, after=20.0, sets=sets, reviews=reviews)
    assert out["verdict"] == se.VERDICT_OPPOSES
    assert out["metrics"]["blocked_review"]["in_better"] == 6


# ---------------------------------------------------------------- 其余两个


def test_intraday_top_limit_is_not_applicable_not_unsupported():
    """L0 展示容量 ⇒ `not_applicable`（评估过、结论是不需要评估），
    不是 `unsupported`（那会让人以为缺能力）。"""
    out = se.eval_intraday_top_limit(before=8, after=12, sets=_sets(5), reviews={})
    assert out["verdict"] == se.VERDICT_NOT_APPLICABLE
    assert "无意义" in out["note"]


def test_max_swaps_gives_impact_only_never_direction():
    """换股上限只给影响面：截断会同时影响换入与换出，
    组合级后果需真实回放，本模块不做 ⇒ 绝不编方向。"""
    sets = [{"date": f"d{i}", "items": [], "rejected": [],
             "replaced": [{"out": "A", "in": "B", "delta": 1.0},
                          {"out": "C", "in": "D", "delta": 1.0}]} for i in range(5)]
    out = se.eval_max_swaps_per_day(before=2, after=1, sets=sets, reviews={})
    assert out["verdict"] == se.VERDICT_NEUTRAL
    assert out["metrics"]["truncated_swaps"] == 5
    assert out["metrics"]["affected_days"] == 5


# ---------------------------------------------------------------- 统一入口


def test_unknown_key_stays_unsupported():
    out = se.evaluate_shadow("no_such_param", before=1, after=2, session_factory=object())
    assert out["verdict"] == "unsupported"


def test_evaluate_shadow_covers_all_non_style_whitelist_params():
    """**定点回归**：白名单里除 style_offsets 外的四个参数都必须有评估器。

    这正是 S2-11 要治的病——只有 1/5 有评估器。新增白名单参数而忘了配评估器即失败。
    """
    try:
        from app.services.agent_params import PARAM_REGISTRY
    except Exception:  # noqa: BLE001
        pytest.skip("无法读取参数白名单")
    expected = set(PARAM_REGISTRY) - {"picks_style_offsets_json"}
    assert expected <= set(se.EVALUATORS), f"缺评估器：{sorted(expected - set(se.EVALUATORS))}"


def test_evaluate_shadow_survives_broken_session_factory():
    """库挂了不能拖垮 promote 流程；且必须**说清是故障**，
    不能伪装成"历史样本不足"——两者处置方式完全不同。"""
    out = se.evaluate_shadow("picks_min_pick_score", before=50.0, after=55.0,
                             session_factory=_BrokenFactory())
    assert out["verdict"] == se.VERDICT_INSUFFICIENT
    assert "失败" in out["note"]
    assert "并非样本不足" in out["note"]


class _BrokenFactory:
    def __call__(self):
        raise RuntimeError("db down")


def test_load_history_reports_error_instead_of_empty_success():
    """读失败必须回传 error —— 空结果 + 无 error 会被下游当成"确实没历史"。"""
    sets, reviews, err = se.load_history(_BrokenFactory())
    assert sets == [] and reviews == {}
    assert err


# ---------------------------------------------------------------- 全市场走势补验（#9）


def test_loosening_gets_a_direction_from_market_gains():
    """**定点回归**：放宽方向此前因补入者无复盘记录而**恒为 neutral**。

    传入 market_gains 后必须能真正给出方向（补入者不弱于保留者 ⇒ supports）。
    """
    sets = _sets(6)
    sim = se.simulate_min_score(sets, 45.0, 5, base_threshold=50.0)
    # 补入者全部跑赢（ratio=1.0），保留者只有一半跑赢（ratio=0.5）⇒ supports。
    # 两边都必须给值：判定是对比，只补一边仍会落到"可比性不足"而 neutral。
    gains = {(a["symbol"], a["date"]): 2.5 for a in sim["added"]}
    for i, k in enumerate(sim["kept"]):
        gains[(k["symbol"], k["date"])] = 1.0 if i % 2 == 0 else -1.0

    out = se.eval_min_pick_score(before=50.0, after=45.0, sets=sets, reviews={},
                                 market_gains=gains)
    assert out["verdict"] == se.VERDICT_SUPPORTS
    assert out["metrics"]["review"]["added_source"] == "marketdb"
    assert out["metrics"]["review"]["added"]["n"] > 0


def test_market_gains_below_zero_count_as_not_good():
    """补入者跑输市场（超额 <0）⇒ 放宽是稀释组合 ⇒ opposes。"""
    sets = _sets(6)
    sim = se.simulate_min_score(sets, 45.0, 5, base_threshold=50.0)
    gains = {(a["symbol"], a["date"]): -1.5 for a in sim["added"]}
    for k in sim["kept"]:
        gains[(k["symbol"], k["date"])] = 1.0  # 保留者全部跑赢
    out = se.eval_min_pick_score(before=50.0, after=45.0, sets=sets, reviews={},
                                 market_gains=gains)
    assert out["verdict"] == se.VERDICT_OPPOSES


def test_raising_ignores_market_gains():
    """抬高方向看的是**剔除者**，与 market_gains 无关（不得被它污染）。"""
    sets = _sets(6)
    sim = se.simulate_min_score(sets, 55.0, 5, base_threshold=50.0)
    gains = {(d["symbol"], d["date"]): 9.9 for d in sim["dropped"]}
    out = se.eval_min_pick_score(before=50.0, after=55.0, sets=sets, reviews={},
                                 market_gains=gains)
    assert out["metrics"]["review"]["added_source"] == "review"


def test_market_gains_missing_keys_are_dropped_not_zeroed():
    """查不到的票必须**剔除**，不能当 0 参与统计（0 = 恰好持平，会稀释结论）。"""
    sets = _sets(6)
    gains = {}  # 一条都查不到
    out = se.eval_min_pick_score(before=50.0, after=45.0, sets=sets, reviews={},
                                 market_gains=gains)
    assert out["metrics"]["review"]["added"]["n"] == 0
    assert out["verdict"] == se.VERDICT_NEUTRAL
    assert "落选池" in out["note"]


def test_forward_horizon_matches_review_caliber():
    """补验窗口必须与 DailyPickReview 的 T+5 口径一致，否则两方向不可比。"""
    assert se.FORWARD_HORIZON == 5


def test_load_market_forward_gains_returns_empty_on_empty_input():
    assert se.load_market_forward_gains([]) == {}
