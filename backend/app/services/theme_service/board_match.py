"""题材 → 东财板块名/码映射（策略 A 最短匹配 + 策略 B 剥后缀，两者刻意不合并）。

`backend/app/services/theme_service.py` 的内部切片（IMP-005 批 4，2026-09-15 从 1383 行单文件按业务域拆出）。
**只搬位置、不重写**：语句正文与前置注释与拆分前逐字符相同（唯一例外见文件内注释）；
对外仍由 `app.services.theme_service` 统一转发，因此引用方零改动。
"""
from __future__ import annotations

from collections.abc import Iterable
from app.market import board_flow

from .core import (
    log,
)
#: 东财板块名的常见后缀，匹配前剥离（"黄金概念" 与 "黄金珠宝" 实际同源）
BOARD_SUFFIXES = ("概念", "板块", "指数", "产业")


def _strip_board_suffix(name: str) -> str:
    for suf in BOARD_SUFFIXES:
        if name.endswith(suf) and len(name) > len(suf) + 1:
            return name[: -len(suf)]
    return name


# ---------------------------------------------------------------------------
# 题材标签 → 板块名：**两套策略刻意并存，勿合并**（R-1，2026-09-12 评审批次 1）
#
# | 策略 | 位置 | 规则 | 用在 |
# |---|---|---|---|
# | **A 最短包含** | `match_board_name_shortest`（本函数） | 精确 → 双向包含取**板块名最短** | 盘中 `watcher.match_board_pct`、回放 `backtest.match_board_name` |
# | **B 剥后缀取最长** | `match_board`（本模块） | 精确 → 剥后缀精确 → 剥后缀后取**最长** | L3 题材看板（`architecture-design.md §2` 指定映射） |
#
# 两者会给出**不同答案**。反例（已固化为守卫
# `tests/test_theme_service.py::test_two_board_match_policies_are_deliberately_divergent`）：
#     tag="AI"、板块=["AI应用", "AI算力芯片"]
#       → A 取 "AI应用"（最短，语义最贴近）
#       → B 取 "AI算力芯片"（最长，更具体）
# 即：**同一个题材标签，在"盘中/回放分析"与"题材看板展示"两条路径上可能落到不同板块**。
#
# ⚠️ 这是**已知且待决断的口径差异，不是疏忽**——合并即改变题材→板块映射结果，
# 属口径变更（本仓纪律：此类改动须先拍板）。故本轮只做两件事：
#   ① 把 A 的两份冗余实现收敛为下方唯一实现（行为等价，有测试与注入验证）；
#   ② 把分叉写成显式契约 + 双向守卫，防止将来有人"顺手清理"时静默合并。
# ---------------------------------------------------------------------------


def match_board_name_shortest(tag: str, names: Iterable[str]) -> str | None:
    """题材标签 → 板块名：**精确优先**；否则「双向包含」里取**板块名最短**的那个。

    最短 = 语义最贴近（「粮食」→「粮食概念」而非「粮食安全概念」）；
    匹配不到 → None（unknown），**绝不拿不相干的板块冒充**。

    **唯一实现**：`watcher.match_board_pct`（盘中，取板块涨幅）与
    `backtest.match_board_name`（回放，取板块名）此前各写一份同口径实现
    ——`backtest` 的模块 docstring 自己就写着"与盘中 `watcher.match_board_pct`
    同口径"。本函数即那份口径的唯一落点（R-1）。两者改为薄委托，行为等价：
    原有的 `test_watcher.py::test_match_board_pct_exact_contains_none` 与
    `test_picks_backtest.py::test_match_board_name_same_semantics_as_watcher`
    就是等价性的回归位。

    与 `match_board`（策略 B）**不是同一政策**，刻意不合并，理由见上方策略表。

    ⚠️ 已知边界（沿袭旧实现，本轮刻意未改）：`tag` 为空串时，
    「双向包含」判定里的 `"" in name` 恒为真 ⇒ 会匹配到**板块名最短的那一个**。
    这属于"空标签不该有归属"的隐患，但修它同样是口径变更，另行决断。
    """
    names = [n for n in names if n]
    if tag in names:
        return tag
    candidates = [n for n in names if tag in n or n in tag]
    if not candidates:
        return None
    return min(candidates, key=len)


def match_board(theme: str, index: dict[str, dict]) -> dict | None:
    """把题材名匹配到东财板块（**策略 B：剥后缀 → 多命中取最长**）。

    ths 题材标签体系与东财板块名体系不同（"转基因玉米" vs "转基因"、
    "黄金珠宝" vs "黄金概念"），精确匹配命中率极低，必须做剥离后缀后的双向包含匹配；
    多命中时取板块名最长的（更具体）。

    ⚠️ 本函数与 `match_board_name_shortest`（策略 A）**政策不同、答案可能不同**，
    刻意并存（策略表与反例见 `match_board_name_shortest` 上方注释块）。
    这是 L3 题材看板指定的映射实现（`app/market/board_flow.py` 层纪律），
    不要因为"看着像重复"而合并——合并会改变题材看板落到哪个板块上。
    """
    if not theme or not index:
        return None
    if theme in index:
        return index[theme]
    t = _strip_board_suffix(theme)
    if t in index:
        return index[t]
    best: dict | None = None
    best_len = 0
    for bname, row in index.items():
        b = _strip_board_suffix(bname)
        if len(b) < 2 or len(t) < 2:
            continue
        if t == b:
            return row
        if t in b or b in t:
            if len(b) > best_len:
                best, best_len = row, len(b)
    return best


async def _board_index() -> dict[str, dict]:
    """板块指标索引：题材名 → 板块指标。行业优先占位，概念补足。

    走 board_flow 唯一入口（板块数据治理规则：任何模块不得自行请求东财板块
    接口），并复用其盘中 30s 缓存。main_net_inflow 以元为单位
    （main_net_yi×1e8），保持 news_persistence「净流入 X 元」的展示口径不变。
    失败返回空 dict → match_board 返回 None → 资金维度按缺失不参与判定（三态）。
    """
    index: dict[str, dict] = {}
    for kind in ("industry", "concept"):
        rows, errs = await board_flow.get_board_list(kind)
        if rows is None:
            log.warning("board list(%s) unavailable: %s", kind, "; ".join(errs))
            continue
        for row in rows:
            name = row.get("name")
            if not name:
                continue
            yi = row.get("main_net_yi")
            mapped = {
                "board_code": row.get("board_code"),
                "name": name,
                "kind": row.get("kind"),
                "change_pct": row.get("change_pct"),
                # main_net_inflow 以元为单位（news_persistence 展示口径）；
                # main_net_yi 保留亿单位（连续流入天数按亿比对落盘 bar，单位必须一致）
                "main_net_yi": yi,
                "main_net_inflow": yi * 1e8 if yi is not None else None,
                "main_net_ratio": row.get("main_net_ratio"),
            }
            if kind == "industry":
                index[name] = mapped
            else:
                index.setdefault(name, mapped)
    return index


async def board_rows_for_names(names: list[str]) -> dict[str, dict]:
    """名字（题材名或东财板块名）→ 东财板块资金行（含**连续流入天数**）。

    L3 映射层的唯一批量入口（P1-5，2026-09-10）：P1-5 传 ths 题材名，
    P1-4 传东财板块代码时走 `board_rows_for_codes`（免名字歧义）。

    数据链路：`_board_index()`（复用 board_flow 盘中 30s 缓存，零额外上游调用）
    → `match_board` 匹配 → `board_flow.get_board_streaks`（纯读落盘，零外呼）。
    匹配不到的名字**不出现在返回里**——调用方按三态处理，绝不臆造；
    连续流入天数只对落盘 Top 板块可判，其余 None（区别于 0=今日净流出）。
    """
    if not names:
        return {}
    index = await _board_index()
    matched: dict[str, dict] = {}
    for n in dict.fromkeys(names):  # 去重保序
        if not n:
            continue
        row = match_board(n, index)
        if row:
            matched[n] = dict(row)
    return _attach_streaks(matched)


async def board_rows_for_codes(codes: list[str]) -> dict[str, dict]:
    """东财板块代码（BKxxxx）→ 板块资金行（含**连续流入天数**）。

    与 `board_rows_for_names` **同一份数据、同一份 30s 缓存**，只是索引键换成
    板块代码——调用方已知代码时走这里，避免名字匹配歧义（东财行业三级名带罗马
    数字后缀「白酒Ⅱ」，而板块榜里是「白酒」；P1-4 主板块即取 L2 行，正好踩到这个差异）。
    """
    if not codes:
        return {}
    index = await _board_index()
    by_code = {
        r["board_code"]: dict(r) for r in index.values() if r.get("board_code")
    }
    matched = {c: by_code[c] for c in dict.fromkeys(codes) if c in by_code}
    return _attach_streaks(matched)


def _attach_streaks(rows: dict[str, dict]) -> dict[str, dict]:
    """批量补连续流入天数（按 f62 亿比对落盘日度 bar；store 无该板块 → None）。"""
    if not rows:
        return rows
    streaks = board_flow.get_board_streaks(
        {r["board_code"]: r.get("main_net_yi") for r in rows.values()}
    )
    for row in rows.values():
        row["streak"] = streaks.get(row["board_code"])
    return rows
