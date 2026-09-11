"""事件判定与状态机测试（2026-09-09 北京十五五规划案例驱动）。

覆盖三条修复：
1. 快讯入库漏传 theme_names → 官方题材目录匹配失效（商业航天新闻零方向行）；
2. 目录名带「概念/板块」后缀匹配不到标题词干（「芯片概念」vs「芯片」）；
3. 判定状态机：judged/pending/neutral(超时收敛)/expired，避免长期停留待判。
"""

from datetime import datetime, timedelta, timezone
from app.events.extract import (
    PENDING_TIMEOUT_HOURS,
    _name_stem,
    build_event,
    judge_state,
)

NOW = datetime(2026, 9, 9, 8, 0, tzinfo=timezone.utc)

# 真实官方目录子集（含带后缀名，验证词干匹配）
THEMES = ["商业航天", "卫星导航", "芯片概念", "低空经济", "人工智能"]


def test_flash_theme_names_required_for_matching():
    """回归：不传 theme_names 时，政策利好匹配不到任何板块（原事故）。"""
    title = "北京：加快发展商业航天产业 突破可重复使用火箭和大推力发动机技术"
    assert build_event(title, source="东财快讯", theme_names=[])["directions"] == []
    rows = build_event(title, source="东财快讯", theme_names=THEMES)["directions"]
    assert [r["target"] for r in rows] == ["商业航天"]
    assert rows[0]["direction"] == 1  # 「加快发展/突破」→ 利好


def test_name_stem_matches_suffixed_catalog_name():
    """目录名「芯片概念」→ 词干「芯片」命中标题；短词干不参与防误命中。"""
    assert _name_stem("芯片概念") == "芯片"
    assert _name_stem("人工智能") == "人工智能"  # 无后缀原样
    assert _name_stem("A") == ""  # 过短不参与
    rows = build_event("美国商务部宣布对中国芯片出口管制", source="东财快讯", theme_names=THEMES)["directions"]
    chip = [r for r in rows if r["target"] == "芯片概念"]
    assert chip and chip[0]["direction"] == -1  # 管制 → 利空
    assert chip[0]["matched_by"] == "name-stem"


def test_policy_category_recognized():
    """规划/印发类正面政策此前归为 other（半衰期 48h），现识别为 policy（336h）。"""
    ev = build_event("工信部印发《十四五智能制造发展规划》", source="东财快讯", theme_names=["智能制造"])
    assert ev["category"] == "policy"
    assert ev["half_life_hours"] == 336


def test_judge_state_transitions():
    """四态流转：有方向=judged；无方向未超时=pending；超时=neutral；过期=expired。"""
    assert judge_state(NOW, [{"direction": 1, "chain": "突破"}], now=NOW)["status"] == "judged"
    assert judge_state(NOW - timedelta(hours=2), [], now=NOW)["status"] == "pending"
    over = NOW - timedelta(hours=PENDING_TIMEOUT_HOURS + 1)
    st = judge_state(over, [], now=NOW)
    assert st["status"] == "neutral"
    # 2026-09-09 时区口径：judged_at 返回北京 naive（NOW 是 UTC aware → 转换后比较）
    over_bj = over.astimezone(timezone(timedelta(hours=8))).replace(tzinfo=None)
    assert st["judged_at"] > over_bj  # 收敛时刻 = 发布 + 超时
    assert judge_state(NOW - timedelta(hours=50), [{"direction": 1, "chain": ""}],
                       now=NOW, half_life_hours=48)["status"] == "expired"


def test_judge_state_handles_naive_datetime():
    """SQLite 取出的 naive 时间不得炸（KB-ENG-08 同族）。"""
    naive = datetime(2026, 9, 9, 8, 0)  # 无 tzinfo
    assert judge_state(naive, [{"direction": -1, "chain": ""}], now=NOW)["status"] == "judged"
