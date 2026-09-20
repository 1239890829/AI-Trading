"""可参与性判定与题材联动挖掘单测（2026-09-15 用户选股口径升级）。

覆盖三块，全部是**纯函数**（零 IO，直接构造输入）：
1. 开盘即涨停 / 可参与性三态（`intraday_opportunity` 与 `picks_pipeline` 共用）
2. 涨停集中度与官方容器挂靠（决定"要不要挖"与"从哪挖"）
3. 联动候选筛选链（决定"谁能进猎场"）——逐条钉住过滤顺序与三态降级

判据写作纪律（本仓）：每个断言都写清"为什么是这个值"，避免只钉实现、不钉语义。
"""
from app.picks.tradability import (
    CANDIDATE_PER_THEME,
    MIN_THEME_LIMIT_UPS,
    PER_THEME,
    assess,
    attach_tradability,
    index_views,
    is_concentrated,
    is_open_sealed,
    linkage_candidates,
    linkage_confidence,
    resolve_containers,
    seal_metrics,
    theme_focus,
)

# ---------------------------------------------------------------- 开盘即涨停


def test_open_sealed_covers_auction_and_first_second():
    # 竞价一字板（09:25 撮合）与开盘秒板都在 09:30 之内 → 全天零买入机会
    assert is_open_sealed("09:25:00") is True
    assert is_open_sealed("09:25:03") is True  # 竞价成交时间戳带秒
    assert is_open_sealed("09:29:59") is True
    # 边界：09:30:00 算开盘即封（开盘瞬间已封死），09:30:01 起算盘中封板
    assert is_open_sealed("09:30:00") is True
    assert is_open_sealed("09:30:01") is False
    assert is_open_sealed("10:31:00") is False


def test_open_sealed_accepts_compact_and_returns_unknown():
    assert is_open_sealed("092500") is True   # ths 无冒号口径
    # 三态纪律：时间缺失是"判不了"，绝不能返回 False（那等于冒充"可以参与"）
    assert is_open_sealed(None) is None
    assert is_open_sealed("") is None
    assert is_open_sealed("abc") is None


def test_assess_sealed_now_is_not_participable():
    r = assess(sealed=True, first_seal_time="09:25:00")
    assert r["level"] == "不可参与" and "竞价即封" in r["basis"] and "当前" in r["basis"]
    assert "全天无买入机会" not in r["basis"]
    # 盘中封板（非开盘即封）同样不可参与，但文案要点明首封时间（可追溯）
    r2 = assess(sealed=True, first_seal_time="10:31:00")
    assert r2["level"] == "不可参与" and "10:31" in r2["basis"]
    # 首封时间缺失不影响"已封板 ⇒ 不可参与"这个判定
    assert assess(sealed=True)["level"] == "不可参与"


def test_assess_unsealed_is_participable():
    assert assess(sealed=False)["level"] == "可参与"
    # 未封板时不看首封时间（炸板回落的票正是"开板重评"的可参与对象）
    assert assess(sealed=False, first_seal_time="09:25:00")["level"] == "可参与"


def test_assess_unknown_current_never_infers_from_first_seal_history():
    """首封时间是历史事实，不足以证明当前仍封或已开；current unknown 必须保持 unknown。"""
    for first in ("09:26:00", "14:20:00", None):
        got = assess(sealed=None, first_seal_time=first)
        assert got["level"] == "unknown"
        assert "current" in got["basis"]


def test_seal_metrics_uses_board_specific_limits():
    # 主板 10cm：封板线 9.7、临板下沿 6.5；创业板 20cm：19.7 / 13.0
    assert seal_metrics("600001", "", 4.5)["limit_pct"] == 10.0
    assert seal_metrics("600001", "", 7.0)["in_pre_limit"] is True
    assert seal_metrics("300001", "", 7.0)["in_pre_limit"] is False
    assert seal_metrics("600001", "", 4.5)["runway_pct"] == 5.2


# ---------------------------------------------------------------- 涨停集中度


def test_is_concentrated_needs_count_and_share():
    assert is_concentrated(3, 30) is True          # 3 家 / 10% 恰好达标
    assert is_concentrated(2, 5) is False          # 家数不足
    assert is_concentrated(3, 60) is False         # 家数够但占比仅 5%
    # 分母缺失（数据拿不到）时不拿"占比 0%"误杀，只按家数判
    assert is_concentrated(3, None) is True
    assert is_concentrated(3, 0) is True


def _stats():
    return {
        "算力": {"symbols": ["a", "b", "c", "d"], "max_boards": 3},
        "机器人": {"symbols": ["e", "f", "g", "h"], "max_boards": 2},
        "零散题材": {"symbols": ["i", "j"], "max_boards": 1},
        "占比不足": {"symbols": ["k", "l", "m"], "max_boards": 1},
    }


def test_theme_focus_picks_by_count_then_boards():
    got = theme_focus(_stats(), limit_up_total=35)
    # 「零散题材」家数不足（2 < 3）；「占比不足」家数够但 3/35=8.6% < 10% → 双双剔除。
    # 同家数时按最高连板降序 ⇒ 算力(4 家, 3 板) 在 机器人(4 家, 2 板) 之前。
    assert [t["theme"] for t in got] == ["算力", "机器人"]
    assert got[0]["count"] == 4 and got[0]["basis"].startswith("题材内涨停 4 家")
    assert round(got[0]["share"], 3) == round(4 / 35, 3)


def test_theme_focus_respects_max_themes():
    stats = {f"t{i}": {"symbols": ["a", "b", "c"], "max_boards": 1} for i in range(6)}
    assert len(theme_focus(stats, limit_up_total=10, max_themes=2)) == 2


# ---------------------------------------------------------------- 官方容器挂靠


def _index():
    """symbol → [(概念 code, name)]：A/B/C 同属小概念 X，BIG 是"全市场性大概念"。"""
    idx = {
        "A": [("X", "小概念"), ("BIG", "融资融券")],
        "B": [("X", "小概念"), ("BIG", "融资融券")],
        "C": [("X", "小概念")],
        "D": [("BIG", "融资融券")],
    }
    # BIG 成分更大（现实中是几百只的融资融券/深股通）：规模判据据此把它挡掉
    for s in ("E", "F", "G"):
        idx[s] = [("BIG", "融资融券")]
    return idx


def test_index_views_both_directions():
    sizes, members = index_views(_index())
    assert sizes["X"] == 3 and sizes["BIG"] == 6
    assert set(members["X"]) == {"A", "B", "C"}


def test_resolve_containers_requires_overlap_and_filters_big_concepts():
    sizes, _members = index_views(_index())
    # 把规模上限定在 X(3) 与 BIG(6) 之间：小概念留下、大概念剔除——
    # 从融资融券成分里挖出来的"联动股"与题材毫无关系（与 official_match 同口径）
    got = resolve_containers(
        {"A", "B", "C"}, _index(), sizes, max_concept_size=5, min_hits=2
    )
    assert [c["code"] for c in got] == ["X"]
    got2 = resolve_containers({"A", "B", "C"}, _index(), sizes)
    assert [c["code"] for c in got2] == ["X", "BIG"]  # 命中数降序，X(3) 在 BIG(2) 前


def test_resolve_containers_single_hit_is_not_a_container():
    sizes, _ = index_views(_index())
    # 只有 1 只成员命中 ⇒ 单票噪声，不挂靠（与 official_match.MIN_HITS 同口径）
    assert resolve_containers({"D"}, _index(), sizes) == []


# ---------------------------------------------------------------- 联动置信度


def test_linkage_confidence_levels():
    # 已进临板区（主板 6.5% 起）且题材成建制 → 高
    assert linkage_confidence(
        theme_limit_ups=4, theme_stage="发酵", pct=7.0, limit_pct=10.0
    )["level"] == "高"
    # 涨幅过了跟进线但未到临板区 → 中
    assert linkage_confidence(
        theme_limit_ups=4, theme_stage="发酵", pct=3.5, limit_pct=10.0
    )["level"] == "中"
    # 题材退潮：基座不支持延续，涨幅再高也不给高
    assert linkage_confidence(
        theme_limit_ups=6, theme_stage="退潮", pct=8.0, limit_pct=10.0
    )["level"] == "低"
    # 未成建制
    assert linkage_confidence(
        theme_limit_ups=2, theme_stage="发酵", pct=8.0, limit_pct=10.0
    )["level"] == "低"
    # 阶段缺失 → unknown（判不出不冒充）
    assert linkage_confidence(
        theme_limit_ups=4, theme_stage=None, pct=8.0, limit_pct=10.0
    )["level"] == "unknown"


# ---------------------------------------------------------------- 联动候选筛选


def _snap(symbol, name, pct, amount=1.0e8, price=10.0):
    return {"symbol": symbol, "name": name, "change_pct": pct, "amount": amount,
            "price": price, "turnover_rate": 3.0}


def _candidates(member_symbols, sealed=(), snaps=None):
    rows = snaps if snaps is not None else [
        _snap("600001", "甲", 3.0),
        _snap("600002", "乙", 6.8),   # 已进临板区（主板 6.5 起）
        _snap("600003", "丙", 0.5),   # 涨幅不足联动下沿
        _snap("600004", "丁", 9.8),   # 已封板线（涨停池滞后时的双保险）
        _snap("600005", "戊", 4.0, amount=1.0e7),  # 成交额不足
    ]
    return linkage_candidates(
        container={"code": "X", "name": "小概念"},
        member_symbols=member_symbols,
        ever_sealed_symbols=set(sealed),
        snapshot_by={r["symbol"]: r for r in rows},
        theme_limit_ups=4,
        theme_stage="发酵",
    )


def test_linkage_candidates_filter_chain():
    got = _candidates(["600001", "600002", "600003", "600004", "600005", "600999"])
    syms = [c["symbol"] for c in got]
    # 600003 涨幅不足 / 600004 已封板 / 600005 成交额不足 / 600999 无快照 → 全剔除
    assert syms == ["600002", "600001"]  # 涨幅降序


def test_linkage_candidates_ever_sealed_without_timed_snapshot_stays_out():
    # 涨停池只证明“今日曾封板”；没有可信 current as-of 时不能声称已经开板，故仍不进候选。
    got = _candidates(["600001", "600002"], sealed={"600002"})
    assert [c["symbol"] for c in got] == ["600001"]


def test_linkage_candidates_carry_tradability_and_basis():
    got = _candidates(["600001", "600002"])
    top = got[0]
    # 每只候选都必须自带"可参与"判定与可追溯依据（卡片直接展示，不二次加工）
    assert top["tradability"]["level"] == "可参与"
    assert top["linkage"]["level"] == "高"      # 6.8% 已进临板区
    assert "当前未封板" in top["basis"] and "距封板" in top["basis"]
    assert top["container"] == "小概念" and top["container_code"] == "X"
    assert top["runway_pct"] == round(9.7 - 6.8, 2)


def test_linkage_candidates_per_theme_cap():
    rows = [_snap(f"6000{i:02d}", f"票{i}", 2.0 + i * 0.1) for i in range(20)]
    got = _candidates([r["symbol"] for r in rows], snaps=rows)
    assert len(got) == PER_THEME


def test_candidate_quota_smaller_than_display_capacity():
    # 盘后候选池配额必须小于展示容量（否则 12 只联动股会挤空候选池的其他来源）
    assert CANDIDATE_PER_THEME < PER_THEME
    assert MIN_THEME_LIMIT_UPS == 3


# ---------------------------------------------------------------- 参考区标注


def test_attach_tradability_without_timed_snapshot_is_unknown():
    """曾封板只是历史身份；无可信 current as-of 时不能硬说仍封板或已经开板。"""
    stocks = [
        {"symbol": "600001", "name": "甲", "first_seal_time": "09:25:00"},
        {"symbol": "600002", "name": "乙", "first_seal_time": "14:20:00"},
        {"symbol": "600003", "name": "丙"},
    ]
    attach_tradability(stocks)
    assert all(s["tradability"]["level"] == "unknown" for s in stocks)
    assert all(s["seal_state"]["current_sealed"] is None for s in stocks)
    assert all("可信时点" in s["tradability"]["basis"] for s in stocks)


def test_attach_tradability_uses_realtime_quote_for_open_board():
    """有实时盘口时以盘口为准：**炸板/开板回落**的票是买得进的，不得说成"买不进"。

    实测背景（2026-09-15）：002491 首封 09:57，11:33 时 +9.5% 已开板——
    涨停池只说"今天封过"，不等于"当前仍封着"。一律写死 sealed=True 是**过度断言**，
    与"所有加入猎场的个股必须实际可参与"的口径正好相悖。
    """
    stocks = [
        {"symbol": "600001", "name": "甲", "first_seal_time": "09:57:00"},   # 仍封着
        {"symbol": "600002", "name": "乙", "first_seal_time": "09:57:00"},   # 已开板
        {"symbol": "600003", "name": "丙", "first_seal_time": "09:25:00"},   # 无盘口
    ]
    snap = {
        "600001": {"change_pct": 10.0},   # ≥ 9.7 封板线
        "600002": {"change_pct": 9.5},    # 打开回落
    }
    attach_tradability(stocks, snap, snapshot_state="ready", snapshot_as_of="2026-09-15T03:33:00+00:00")
    assert stocks[0]["tradability"]["level"] == "不可参与"
    assert stocks[1]["tradability"]["level"] == "可参与"
    assert "当前开板" in stocks[1]["tradability"]["basis"]
    # 无 current 报价：只知道“今日曾封板”，当前状态不可判，不能伪造仍封板。
    assert stocks[2]["tradability"]["level"] == "unknown"
    assert stocks[2]["seal_state"]["current_sealed"] is None


# ---------------------------------------------------------------- 板块权限（账户级约束）


def test_board_key_covers_all_trading_boards():
    """板块键：分类单点 = `halt_risk.board_of`，本模块只**追加** B 股识别。

    ⚠️ 为什么 B 股要单独判别：`board_of` 的兜底分支会把 900xxx/200xxx 归成
    `sz_main`，而 B 股需单独账户权限 ⇒ 会被误当"可交易"。实测 2026-09-15。
    """
    from app.picks.tradability import BOARD_LABELS, board_key, board_label

    assert board_key("600519") == "sh_main"
    assert board_key("601398") == "sh_main"
    assert board_key("000858") == "sz_main"
    assert board_key("002491") == "sz_main"
    assert board_key("300662") == "gem"
    assert board_key("301325") == "gem"
    assert board_key("688155") == "star"
    assert board_key("920522") == "bse"
    assert board_key("900901") == "sh_b"
    assert board_key("200011") == "sz_b"
    assert board_key("600001", "ST某某") == "st"
    # 中文名齐全（未知键回退原键，不臆造）
    for key in ("sh_main", "sz_main", "st", "gem", "star", "bse", "sh_b", "sz_b"):
        assert BOARD_LABELS[key]
    assert board_label("300662") == "创业板"


def test_is_tradable_only_main_board():
    """账户当前只开沪深主板（用户 2026-09-15「只有主板的权限现在」）。"""
    from app.picks.tradability import is_tradable

    assert is_tradable("600519") is True
    assert is_tradable("000858") is True
    assert is_tradable("002491") is True
    assert is_tradable("600001", "ST某某") is True  # 主板 ST 仍可交易（涨限已并轨）
    for sym in ("300662", "301325", "688155", "920522", "900901", "200011"):
        assert is_tradable(sym) is False, sym


def test_linkage_candidates_filters_board_and_audits():
    """板块权限在**生产端**就挡住：非主板成分不产出，且挡下几只必须可查。

    挡在候选生产端而不是展示端，是因为"候选少"与"没数据"在页面上同形——
    审计出参是唯一能区分两者的东西。
    """
    from app.picks.tradability import linkage_candidates

    rows = [
        _snap("600001", "主板甲", 3.0),
        _snap("000002", "主板乙", 4.0),
        _snap("300003", "创业丙", 5.0),   # 涨幅最高但**无权限**
        _snap("688004", "科创丁", 6.0),   # 同上
        _snap("920005", "北交戊", 7.0),   # 同上
    ]
    stats: dict = {}
    got = linkage_candidates(
        container={"code": "X", "name": "小概念"},
        member_symbols=[r["symbol"] for r in rows],
        ever_sealed_symbols=set(),
        snapshot_by={r["symbol"]: r for r in rows},
        theme_limit_ups=3,
        theme_stage="发酵",
        stats=stats,
    )
    assert [c["symbol"] for c in got] == ["000002", "600001"]
    assert all(c["board"] in ("沪市主板", "深市主板") for c in got)
    assert stats["excluded_board"] == 3
    assert stats["excluded_board_labels"] == {"创业板": 1, "科创板": 1, "北交所": 1}


def test_linkage_candidate_audit_keeps_each_filter_reason():
    """聚合计数不够回放：每只被挡标的必须保留独立事实与失败层。"""
    from app.picks.tradability import linkage_candidates

    rows = [
        _snap("600001", "主板甲", 3.0),
        _snap("300002", "创业乙", 5.0),
        _snap("600003", "主板丙", 0.2),
    ]
    audit: list[dict] = []
    got = linkage_candidates(
        container={"code": "X", "name": "小概念"},
        member_symbols=[r["symbol"] for r in rows] + ["600099"],
        ever_sealed_symbols=set(), snapshot_by={r["symbol"]: r for r in rows},
        theme_limit_ups=3, theme_stage="发酵", audit_rows=audit,
    )
    assert [r["symbol"] for r in got] == ["600001"]
    by = {r["symbol"]: r for r in audit}
    assert by["600001"]["candidate_decision"] == "included"
    assert by["300002"]["hard_gate_decision"] == "rejected"
    assert by["300002"]["facts"]["board_tradable"] is False
    assert by["600003"]["hard_gate_decision"] == "passed"
    assert by["600003"]["candidate_decision"] == "rejected"
    assert by["600099"]["candidate_decision"] == "unknown"


def test_ever_sealed_open_board_reenters_with_fresh_snapshot():
    audit=[]
    got = linkage_candidates(
        container={"code":"X","name":"小概念"},
        member_symbols=["600002"],
        ever_sealed_symbols={"600002"},
        snapshot_by={"600002": _snap("600002","乙",9.2)},
        theme_limit_ups=4, theme_stage="发酵",
        snapshot_state="ready", snapshot_as_of="2026-09-21T02:31:00+00:00",
        audit_rows=audit,
    )
    assert [c["symbol"] for c in got] == ["600002"]
    st=got[0]["seal_state"]
    assert st["ever_sealed"] is True and st["current_sealed"] is False
    assert st["version"] == "2026-09-21T02:31:00+00:00"
    assert "曾封板" in got[0]["tradability"]["basis"]
    assert "不保证成交" in got[0]["tradability"]["basis"]


def test_ever_sealed_requires_fresh_timed_snapshot_before_reentry():
    for state, asof in [("stale", "2026-09-21T02:31:00+00:00"), ("ready", None)]:
        audit=[]
        got = linkage_candidates(
            container={"code":"X","name":"小概念"}, member_symbols=["600002"],
            ever_sealed_symbols={"600002"}, snapshot_by={"600002": _snap("600002","乙",9.2)},
            theme_limit_ups=4, theme_stage="发酵", snapshot_state=state, snapshot_as_of=asof,
            audit_rows=audit,
        )
        assert got == []
        assert audit[0]["candidate_decision"] == "unknown"


def test_open_reseal_reopen_versions_and_member_dedup_are_consistent():
    def run(pct, version):
        audit=[]
        got=linkage_candidates(
            container={"code":"X","name":"小概念"},
            member_symbols=["600002","600002"], ever_sealed_symbols={"600002"},
            snapshot_by={"600002": _snap("600002","乙",pct)},
            theme_limit_ups=4, theme_stage="发酵", snapshot_state="ready", snapshot_as_of=version,
            audit_rows=audit,
        )
        return got,audit
    opened,_=run(9.2,"v1")
    resealed,a2=run(9.8,"v2")
    reopened,_=run(9.1,"v3")
    assert len(opened)==1 and opened[0]["seal_state"]["version"]=="v1"
    assert resealed==[] and a2[0]["hard_gate_decision"]=="rejected"
    assert len(reopened)==1 and reopened[0]["seal_state"]["version"]=="v3"


def test_attach_tradability_does_not_use_stale_quote_to_claim_open_board():
    stocks=[{"symbol":"600002","name":"乙","first_seal_time":"09:57:00"}]
    snap={"600002":{"change_pct":9.2}}
    attach_tradability(stocks, snap, snapshot_state="stale", snapshot_as_of="v1")
    assert stocks[0]["tradability"]["level"] == "unknown"
    attach_tradability(stocks, snap, snapshot_state="ready", snapshot_as_of="v2")
    assert stocks[0]["tradability"]["level"] == "可参与"
    assert stocks[0]["seal_state"] == {
        "ever_sealed": True, "current_sealed": False, "snapshot_state": "ready",
        "version": "v2",
    }
