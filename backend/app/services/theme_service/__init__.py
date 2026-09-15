"""题材梯队看板：把涨停池从「平铺列表」重组为「题材容器 + 连板天梯」。

设计原则
--------
1. **题材归因以同花顺官方口径为准**：ths `limit_up_pool.reason` 是「+」分隔的题材串，
   东财涨停池没有这个字段。字段缺失时该股归入「未分类」，不臆造题材。
2. **所有可证伪的判断都必须能从数据反推**：角色、阶段、健康度都是规则化输出，
   并在响应里附带 `basis`（判定依据），便于人工复核与回测。
3. **纯计算与 IO 分离**：本模块上半部分是不依赖网络/数据库的纯函数（可直接单测），
   `build_theme_board` 才做 IO 编排。

数据源分工（详见 docs/data-sources.md §3）
------------------------------------------
- 题材归因 / 连板数 / 封单额：ths limit_up_pool
- 换手率 / 开板次数 / 末封时间 / 流通市值 / 成交额 / 行业：东财 push2ex
- 板块涨跌幅 / 涨跌家数 / 成交额 / 主力净流入 / 领涨股：东财 push2delay 板块列表
- 题材连续活跃天数：近 N 日 ths 涨停池（自建，衡量「资金是否持续」）
- 接力赚钱效应（溢价）：前一日题材涨停股 + 全市场快照

已知边界（不可臆造）
--------------------
- 板块的 3/5/10 日涨跌幅原为东财字段序推断；2026-08-31 起由路由层用同花顺
  官方板块 K 线交叉验证并替换（board.multi_day_verified=true，推断值保留在
  chg_*_inferred 供审计，见 market.py _verify_board_multi_day）。
  目录服务不可用/题材不在目录时保留推断值并如实标注。
- 炸板池无法按题材归属（东财/ths 炸板池都不带题材），题材级只能用「开板过的涨停股占比」
  近似，全市场炸板率另行给出作为背景。

切片说明（IMP-005 批 4，2026-09-15）
--------------------------------
本包是 `backend/app/services/theme_service.py`（原 1383 行单文件）按业务域拆出的内部切片，
**本文件只做转发、不含实现**。分片：
- `core.py` —— 共享基础设施：日志句柄与 provider 定位（被 board_match / board_build 共用）
- `normalize.py` —— 题材标签归一化与封板时间解析（纯函数：归一化词典 / 封板档位 / 早封率 / 留存率）
- `formation.py` —— 成建制分级与题材强弱（纯函数：成建制门槛 / 打折系数 / 强弱分级 / 强度评分）
- `roles.py` —— 梯队角色、梯队完整度与题材阶段（纯函数）
- `health.py` —— 封板质量与题材健康度（纯函数）
- `attribution.py` —— 梯队联动归属：每只涨停股只归属一个主题材（纯函数）
- `board_match.py` —— 题材 → 东财板块名/码映射（策略 A 最短匹配 + 策略 B 剥后缀，两者刻意不合并）
- `card.py` —— 单张题材卡片的构建：连板天梯 / 强度指标 / 龙头与候选（纯计算，零 IO）
- `board_build.py` —— 看板 IO 编排：并发取数 + 逐题材建卡（`build_theme_board` 及其取数辅助）
- `checklist.py` —— 介入条件清单：从看板 payload 解析某只股票的介入条件（纯函数，零 IO）

⚠️ **Python 没有 `export`**：原文件的所有顶层名（含 `_market_break_rate` /
`_auction_gaps` / `_pick_provider` / `_em_enhancement_map` 这类**私有名**，实测被
`tests/` 与 `app/` 直接 import）都必须在外部可见面上 —— 因此这里 re-export 全部名字，
并由 `__all__` 显式登记，避免「看起来没漏、其实少了几个」的静默漂移。
"""
from __future__ import annotations

from .core import (
    _pick_provider,
    log,
)

from .normalize import (
    EARLY_SEAL_CUTOFF_HHMMSS,
    SEAL_PHASES,
    THEME_ALIASES,
    early_seal_rate,
    normalize_theme,
    parse_hhmmss,
    parse_theme_tags,
    seal_phase,
    seal_retention_rate,
)

from .formation import (
    FORMATION_DAMPING,
    FORMATION_LEVELS,
    formation_level,
    strength_tier,
    theme_strength_score,
)

from .roles import (
    MIDDLE_WEIGHT_MIN_CAP,
    REPAIR_MIN_LEADER_BOARDS,
    ROLE_ORDER,
    classify_role,
    echelon_completeness,
    judge_theme_stage,
)

from .health import (
    seal_quality_score,
    theme_health_note,
)

from .attribution import (
    UNCLASSIFIED,
    assign_primary_themes,
)

from .board_match import (
    BOARD_SUFFIXES,
    _attach_streaks,
    _board_index,
    _strip_board_suffix,
    board_rows_for_codes,
    board_rows_for_names,
    match_board,
    match_board_name_shortest,
)

from .card import (
    _build_card,
    _median,
)

from .board_build import (
    _auction_gaps,
    _em_enhancement_map,
    _load_history,
    _market_break_rate,
    _theme_stats,
    build_theme_board,
)

from .checklist import (
    entry_checklist_from_board,
)

__all__ = [
    "BOARD_SUFFIXES",
    "EARLY_SEAL_CUTOFF_HHMMSS",
    "FORMATION_DAMPING",
    "FORMATION_LEVELS",
    "MIDDLE_WEIGHT_MIN_CAP",
    "REPAIR_MIN_LEADER_BOARDS",
    "ROLE_ORDER",
    "SEAL_PHASES",
    "THEME_ALIASES",
    "UNCLASSIFIED",
    "_attach_streaks",
    "_auction_gaps",
    "_board_index",
    "_build_card",
    "_em_enhancement_map",
    "_load_history",
    "_market_break_rate",
    "_median",
    "_pick_provider",
    "_strip_board_suffix",
    "_theme_stats",
    "assign_primary_themes",
    "board_rows_for_codes",
    "board_rows_for_names",
    "build_theme_board",
    "classify_role",
    "early_seal_rate",
    "echelon_completeness",
    "entry_checklist_from_board",
    "formation_level",
    "judge_theme_stage",
    "log",
    "match_board",
    "match_board_name_shortest",
    "normalize_theme",
    "parse_hhmmss",
    "parse_theme_tags",
    "seal_phase",
    "seal_quality_score",
    "seal_retention_rate",
    "strength_tier",
    "theme_health_note",
    "theme_strength_score",
]
