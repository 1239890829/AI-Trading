"""相位常量收敛的守卫（S2-7，2026-09-11）。

**病根**：市场相位字符串此前在 9 处被重复列举（5 个常量 + 4 处 inline），
并且**已经真实漂移出两处缺陷**——`predict/engine.py` 的 `PHASE_ENV_SCORE` 与
`services/experiments.py` 的 `_SHADOW_PHASES` 里都写着 **「启动」**：

- 「启动」是**题材阶段**（`theme_service.judge_theme_stage`：启动/发酵/高潮/分歧/退潮），
  **不是市场相位**（`sentiment.engine.PHASE_ORDER`：冰点/修复/发酵/高潮/分歧/退潮）。
  两套概念混用的直接后果是「启动」成了永不命中的死键；
- 而真正的 **「修复」相位因此缺失**：预测引擎对修复期一直走 `.get(phase, 0.5)`
  默认值，影子权重的逐相位校验从来没查过修复期。**这个洞是静默的**。

相位是**交易信号级**输入：闸门按「退潮/冰点」撤买入区间、仓位引擎按相位给分。
任一处集合打错，就是同一天两个模块对同一相位给出相反动作。

**本文件钉住**：
1. 三组语义集合互斥、并集恰为 `PHASE_ORDER`（新增相位必须显式归类）；
2. 各模块的相位打分表键集 == `PHASE_ORDER`（缺键 = 走默认值 = 静默漏判）；
3. 源码里不得再出现第二份相位字面量集合（防回潮）。
"""

from __future__ import annotations

import re
from pathlib import Path

from app.sentiment.engine import (
    ADVERSE_PHASES,
    PHASE_ORDER,
    SEVERE_PHASES,
    STRONG_PHASES,
)

_APP = Path(__file__).resolve().parents[1] / "app"


# ---------------------------------------------------------------- 集合自身一致性


def test_phase_sets_partition_phase_order():
    """三组语义集合必须**互斥**且并集恰为 PHASE_ORDER（不重不漏）。"""
    adverse, strong = set(ADVERSE_PHASES), set(STRONG_PHASES)

    assert not (adverse & strong), f"强弱相位重叠：{sorted(adverse & strong)}"
    # 「分歧」是唯一的中性相位：既不在强势组也不在弱势组。
    # 它没被归入任何一组是**刻意的**——分歧期只做最强前排，语义上既非进攻也非空仓。
    assert adverse | strong | {"分歧"} == set(PHASE_ORDER), (
        f"集合未覆盖全部相位或含多余相位："
        f"缺 {sorted(set(PHASE_ORDER) - adverse - strong - {'分歧'})}，"
        f"多 {sorted((adverse | strong) - set(PHASE_ORDER))}"
    )


def test_severe_is_strict_subset_of_adverse():
    assert set(SEVERE_PHASES) < set(ADVERSE_PHASES), (
        "SEVERE_PHASES 必须是 ADVERSE_PHASES 的真子集——"
        "「极端」的定义就是「弱势」里更窄的那一档"
    )


def test_phase_sets_have_no_theme_stage_leak():
    """相位集合里不得混入题材阶段名（`启动` 只属于题材阶段）。"""
    stage_only = {"启动"}
    for name, group in (
        ("ADVERSE_PHASES", ADVERSE_PHASES),
        ("SEVERE_PHASES", SEVERE_PHASES),
        ("STRONG_PHASES", STRONG_PHASES),
    ):
        leaked = stage_only & set(group)
        assert not leaked, f"{name} 混入了题材阶段名：{sorted(leaked)}"


# ---------------------------------------------------------------- 打分表键集


def test_predict_env_score_covers_all_phases():
    """预测引擎的环境分表必须覆盖全部相位。

    缺键 ⇒ `.get(phase, 0.5)` 静默给默认值 ⇒「这个相位没有专门环境分」这件事
    永远不会被发现（修复期就是这样被漏了一整个版本的）。
    """
    from app.predict.engine import PHASE_ENV_SCORE

    missing = sorted(set(PHASE_ORDER) - set(PHASE_ENV_SCORE))
    extra = sorted(set(PHASE_ENV_SCORE) - set(PHASE_ORDER))
    assert not missing and not extra, (
        f"PHASE_ENV_SCORE 与 PHASE_ORDER 不一致 —— 缺（会走默认值）：{missing}；"
        f"多（死键，永不命中）：{extra}"
    )


def test_style_router_routes_cover_all_phases():
    from app.picks.style_router import DEFAULT_ROUTES

    missing = sorted(set(PHASE_ORDER) - set(DEFAULT_ROUTES))
    assert not missing, f"风格路由表缺相位（该相位将不被路由）：{missing}"


def test_pick_engine_phase_score_covers_all_phases():
    """`picks/engine.PHASE_SCORE` 是**直接索引**（`[market_phase]`），缺键即 KeyError。"""
    from app.picks.engine import PHASE_SCORE

    missing = sorted(set(PHASE_ORDER) - set(PHASE_SCORE))
    extra = sorted(set(PHASE_SCORE) - set(PHASE_ORDER))
    assert not missing and not extra, (
        f"PHASE_SCORE 与 PHASE_ORDER 不一致 —— 缺（会 KeyError）：{missing}；多：{extra}"
    )


def test_shadow_phases_cover_all_phases():
    """影子权重的逐相位校验必须覆盖全部相位（此前漏掉「修复」）。"""
    from app.services.experiments import _SHADOW_PHASES

    missing = sorted(set(PHASE_ORDER) - set(_SHADOW_PHASES))
    extra = sorted(set(_SHADOW_PHASES) - set(PHASE_ORDER))
    assert not missing and not extra, (
        f"_SHADOW_PHASES 与 PHASE_ORDER 不一致 —— 缺（该相位不被校验）：{missing}；"
        f"多（幽灵相位）：{extra}"
    )


# ---------------------------------------------------------------- 防回潮（源码级）

#: 市场相位**独有**的名字。`发酵/高潮/分歧/退潮` 同时是题材阶段名
#: （`judge_theme_stage` 产出），纯词法扫描无法区分两套词汇——所以只在
#: 一行里出现「市场相位独有名字」时才判定为相位集合，避免误伤题材阶段代码
#: （如 `theme_stage in ("发酵", "高潮")`，那是合法且正确的写法）。
_MARKET_ONLY_PHASES = ("冰点", "修复")

#: 允许存在的模块级相位**打分表**：它们是穷举表而非成员判定集合，
#: 键集由上方 `test_*_covers_all_phases` 单独守卫。
_ALLOWED_TABLES = ("PHASE_ENV_SCORE", "PHASE_SCORE")


def test_no_second_literal_phase_set_in_source():
    """源码里不得再写第二份相位**成员判定集合**。

    判定口径（刻意保守，宁漏勿误伤）：
    - 一行里出现 ≥2 个相位名，**且其中至少一个是市场相位独有名（冰点/修复）**；
    - 排除唯一权威自身、模块级打分表（`_ALLOWED_TABLES`）、注释与文档字符串。

    单相位比较（`phase == "分歧"`）不在此规则内——它清晰且不会漂移。
    """
    offenders: list[str] = []

    for path in _APP.rglob("*.py"):
        rel = path.relative_to(_APP).as_posix()
        if rel == "sentiment/engine.py":  # 唯一权威自身
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            if not code.strip() or '"""' in code:
                continue
            if any(code.lstrip().startswith(f"{t} = ") for t in _ALLOWED_TABLES):
                continue
            hits = [p for p in PHASE_ORDER if f'"{p}"' in code]
            if len(hits) >= 2 and any(p in hits for p in _MARKET_ONLY_PHASES):
                offenders.append(f"{rel}:{lineno} 同时出现 {hits} —— {line.strip()[:80]}")

    assert not offenders, (
        "检测到第二份相位字面量集合（S2-7 的收敛被撤销）：\n" + "\n".join(offenders)
    )


def test_phase_constants_are_imported_not_redeclared():
    """`WEAK_PHASES` / `_EBB_PHASES` 这类本地别名必须**绑定到权威**而非重写字面量。"""
    checks = {
        "picks/gate.py": ["WEAK_PHASES = _ADVERSE", "SEVERE_PHASES = _SEVERE", "STRIP_PHASES = _ADVERSE"],
        "picks/intraday_rules.py": ["ADVERSE_PHASES as _EBB_PHASES"],
        "services/experiments.py": ["tuple(PHASE_ORDER)"],
    }
    for rel, needles in checks.items():
        src = (_APP / rel).read_text(encoding="utf-8")
        for needle in needles:
            assert needle in src, f"{rel} 缺少与权威绑定的写法：{needle}"
        # 反向：不得残留硬编码的相位二元组
        assert not re.search(r'\(\s*"退潮"\s*,\s*"冰点"\s*\)', src), (
            f"{rel} 又写回了硬编码的相位二元组"
        )
