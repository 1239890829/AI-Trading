"""盘中机会视图（intraday_opportunity）单测：辨识度/确定性三态判定与组装。"""
from app.picks.intraday_opportunity import assemble, certainty, distinctiveness


# ---------------------------------------------------------------- distinctiveness


def test_distinct_top10_hot_rank_is_high():
    r = distinctiveness(hot_rank=3, hot_available=True, boards=1, role="首板")
    assert r["level"] == "高"
    assert "人气榜第 3 名" in r["basis"]


def test_distinct_rank_mid_plus_boards_high():
    # 榜 15 名不足高，4 板也不足高（<5）→ 中
    assert distinctiveness(hot_rank=15, hot_available=True, boards=4, role="跟风")["level"] == "中"
    # 5 板高度独立把档位顶到高
    r = distinctiveness(hot_rank=15, hot_available=True, boards=5, role="跟风")
    assert r["level"] == "高"
    assert "5 板高度" in r["basis"]


def test_distinct_role_notable_without_hot_is_mid():
    r = distinctiveness(hot_rank=None, hot_available=True, boards=1, role="龙头")
    assert r["level"] == "中"
    assert "角色 龙头" in r["basis"]


def test_distinct_nothing_is_low():
    r = distinctiveness(hot_rank=None, hot_available=True, boards=1, role="跟风")
    assert r["level"] == "低"


def test_distinct_hot_unavailable_degrades_to_unknown():
    r = distinctiveness(hot_rank=None, hot_available=False, boards=2, role="中军")
    assert r["level"] == "unknown"
    assert "人气榜不可用" in r["basis"]


def test_distinct_hot_unavailable_but_height_still_high():
    # 高度本身就是硬证据：源挂了也不把 6 板压成 unknown
    r = distinctiveness(hot_rank=None, hot_available=False, boards=6, role="空间板")
    assert r["level"] == "高"


def test_distinct_rank_31_is_not_evidence():
    # 31 名开外：榜内但无档位加成，靠别的证据
    assert distinctiveness(hot_rank=31, hot_available=True, boards=1, role="跟风")["level"] == "低"
    assert distinctiveness(hot_rank=31, hot_available=True, boards=2, role="跟风")["level"] == "中"


# ---------------------------------------------------------------- certainty


def test_certainty_ferment_early_strong_seal_is_high():
    r = certainty(
        theme_stage="发酵", formation="成建制", has_succession=True,
        break_count=0, seal_amount=2.0e8, amount=1.0e9, first_seal_time="09:45",
    )
    assert r["level"] == "高"
    assert "题材发酵" in r["basis"] and "首封 09:45" in r["basis"]


def test_certainty_breaks_twice_is_low_even_in_ferment():
    r = certainty(
        theme_stage="发酵", formation="成建制", has_succession=True,
        break_count=2, seal_amount=5.0e8, amount=1.0e9, first_seal_time="09:31",
    )
    assert r["level"] == "低"
    assert "炸板 2 次" in r["basis"]


def test_certainty_stage_base_low_wins():
    # 退潮期再漂亮的封板也不给高（题材基座压个股）
    r = certainty(
        theme_stage="退潮", formation="零散", has_succession=False,
        break_count=0, seal_amount=3.0e8, amount=1.0e9, first_seal_time="09:40",
    )
    assert r["level"] == "低"


def test_certainty_late_weak_seal_is_low():
    r = certainty(
        theme_stage="高潮", formation="成建制", has_succession=True,
        break_count=0, seal_amount=2.0e7, amount=2.0e9, first_seal_time="14:35",
    )
    assert r["level"] == "低"
    assert "首封 14:35" in r["basis"]


def test_certainty_seal_ratio_counts_as_strong():
    # 封单额不足 1 亿，但占成交额比例 ≥10% 同样算强封
    r = certainty(
        theme_stage="发酵", formation="成建制", has_succession=True,
        break_count=0, seal_amount=5.0e7, amount=4.0e8, first_seal_time="10:00",
    )
    assert r["level"] == "高"


def test_certainty_missing_stage_is_unknown():
    r = certainty(
        theme_stage=None, formation=None, has_succession=None,
        break_count=0, seal_amount=None, amount=None, first_seal_time=None,
    )
    assert r["level"] == "unknown"


def test_certainty_mid_fallback():
    # 发酵 + 炸过 1 次：不到高也不至于低 → 中，依据可追溯
    r = certainty(
        theme_stage="发酵", formation="初步成形", has_succession=True,
        break_count=1, seal_amount=2.0e8, amount=1.0e9, first_seal_time="09:50",
    )
    assert r["level"] == "中"
    assert "炸板 1 次" in r["basis"]


# ---------------------------------------------------------------- assemble


def _board() -> dict:
    return {
        "trade_date": "2026-09-03",
        "summary": {"limit_up_total": 43, "market_max_boards": 5, "top_theme": "存储芯片"},
        "caveats": ["测试告警"],
        "themes": [
            {
                "theme": "存储芯片", "stage": "发酵",
                "stage_basis": ["梯队完整"], "strength_score": 82.0,
                "strength_tier": "领涨", "tier_basis": "涨停 12 家",
                "formation": "成建制", "health_note": "健康", "risks": ["注意高位分歧"],
                "performance": {
                    "max_boards": 5, "limit_up_count": 12, "has_succession": True,
                },
                "leaders": {
                    # 刻意与 ladder 首行**同 symbol 但不同文案**：证明 reason 取自 ladder
                    # 行自带的官方涨停原因，而不是从这里按 symbol 匹配（那曾是错误链路，
                    # 且 candidates 只含「补涨/反包」角色 ⇒ 其余角色恒为 None）。
                    "candidates": [
                        {"symbol": "600111", "name": "北方稀土", "boards": 5, "role": "龙头",
                         "reason": "错误来源：不应被采用"},
                    ],
                },
                "ladder": [
                    {"symbol": "600111", "name": "北方稀土", "role": "龙头", "boards": 5,
                     "seal_amount": 2.0e8, "amount": 1.0e9, "break_count": 0,
                     "first_seal_time": "09:45", "change_pct": 10.01,
                     "reason": "存储芯片+稀土永磁"},
                    {"symbol": "000001", "name": "平安银行", "role": "中军", "boards": 2,
                     "seal_amount": 5.0e6, "amount": 8.0e8, "break_count": 3,
                     "first_seal_time": "13:20", "change_pct": 10.0,
                     "reason": "银行+中特估"},
                ],
            },
        ],
    }


def test_assemble_happy_path():
    hot = [{"rank": 2, "symbol": "600111", "name": "北方稀土", "heat": 1234.0, "rank_change": 1, "ts": None}]
    out = assemble(_board(), hot, True, top_themes=5, stocks_per_theme=8)

    assert out["trade_date"] == "2026-09-03"
    assert out["hot_available"] is True
    assert out["summary"]["limit_up_total"] == 43
    assert out["caveats"] == ["测试告警"]

    t = out["themes"][0]
    assert t["theme"] == "存储芯片" and t["stage"] == "发酵"
    assert t["max_boards"] == 5 and t["has_succession"] is True

    s0, s1 = t["stocks"]
    assert s0["hot_rank"] == 2
    assert s0["distinctiveness"]["level"] == "高"
    assert s0["certainty"]["level"] == "高"
    assert s0["reason"] == "存储芯片+稀土永磁"  # ladder 行自带的官方涨停原因

    # 无榜 + 炸板 3 次：辨识度看角色（中军→中），确定性压到低
    assert s1["hot_rank"] is None
    assert s1["distinctiveness"]["level"] == "中"
    assert s1["certainty"]["level"] == "低"


def test_assemble_reason_comes_from_ladder_row_not_leaders_candidates():
    """回归（2026-09-10 用户反馈「入选原因是空的」）：

    旧实现从 ``leaders.candidates`` 建 symbol→reason 映射，而该列表只含「补涨/反包」
    两种角色（低位替代语义槽）⇒ 龙头/中军/空间板/首板… 全部 reason=None。
    正确来源是 **ladder 行自己的 reason**（同花顺官方涨停原因原串）。
    """
    out = assemble(_board(), [], False, top_themes=5, stocks_per_theme=8)
    s0, s1 = out["themes"][0]["stocks"]
    # 同 symbol 的 candidates 文案必须**不**被采用
    assert s0["reason"] == "存储芯片+稀土永磁"
    assert s0["reason"] != "错误来源：不应被采用"
    # 旧实现下「中军」不在 candidates 里 ⇒ None；现在必须有值
    assert s1["reason"] == "银行+中特估"


def test_attach_risk_fields_fills_price_stop_and_exit():
    """现价/止损/出场补全：两处端点（opportunities / top）共用同一实现。"""
    from app.picks.intraday_opportunity import attach_risk_fields

    stocks = [{"symbol": "600111", "name": "北方稀土", "role": "龙头"},
              {"symbol": "000001", "name": "平安银行", "role": "中军"}]
    attach_risk_fields(stocks, {"600111": {"symbol": "600111", "price": 32.5}})

    assert stocks[0]["price"] == 32.5
    assert stocks[0]["stop_ref"] is not None
    assert stocks[0]["stop_ref"]["price"] < 32.5
    assert stocks[0]["exit_plan"]  # 出场纪律结构非空
    # 快照里没有的票：现价显式 None（三态），止损随之降级为 None —— 不臆造
    assert stocks[1]["price"] is None
    assert stocks[1]["stop_ref"] is None


def test_assemble_hot_unavailable_marks_unknown_and_caveat():
    out = assemble(_board(), [], False)
    assert out["hot_available"] is False
    assert any("热股榜不可用" in c for c in out["caveats"])
    s0 = out["themes"][0]["stocks"][0]
    # 6 板都没到（5 板才高），5 板龙头：高度 5 → 高，不受 unknown 影响
    assert s0["distinctiveness"]["level"] == "高"
    s1 = out["themes"][0]["stocks"][1]
    assert s1["distinctiveness"]["level"] == "unknown"


def test_assemble_empty_board_is_honest():
    out = assemble({"trade_date": "2026-09-03", "themes": [], "summary": {}}, [], False)
    assert out["themes"] == []
    assert out["hot_available"] is False
